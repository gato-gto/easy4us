#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
easy4us — массовая расшифровка ionCube-файлов через easytoyou.eu.
Работает на Linux / macOS / Windows (проверялось на Debian 12 и Ubuntu 22.04).
"""

import gzip
import os
import argparse
import shutil
import time
import random
import urllib.parse
import zipfile
from io import BytesIO

import brotli
import requests
import bs4
import zstandard as zstd

# ─────────────── CLI ───────────────
parser = argparse.ArgumentParser(
    usage="easy4us",
    description="Массовая расшифровка ionCube-файлов через easytoyou.eu"
)
parser.add_argument("-u", "--username", required=True, help="логин easytoyou.eu")
parser.add_argument("-p", "--password", required=True, help="пароль easytoyou.eu")
parser.add_argument("-s", "--source", required=True, help="папка-источник")
parser.add_argument("-o", "--destination", default="", help="папка-назначения (по умолчанию *_decoded)")
parser.add_argument("-d", "--decoder", default="ic11php72", help="декодер (по умолчанию ic11php72)")
parser.add_argument("-w", "--overwrite", action="store_true", help="перезаписывать уже существующие")
args = parser.parse_args()
base_url = "https://easytoyou.eu"

# ─────────────── константы ───────────────
MAX_RETRY = 5  # максимум повторов при ошибке / 429
WAIT_SEC = 20  # базовая задержка
JITTER_SEC = 5  # случайная дельта ±5 с
TIMEOUT_GET = 30
TIMEOUT_POST = 120
TIMEOUT_DOWN = 60
BATCH_SIZE = 25  # файлов .php за одну отправку

IONCUBE_MARKERS = (
    b"ionCube Loader",
    b"ionCube PHP Loader",
    b"//001", b"//002"  # сигнатуры v11
)

# ─────────────── HTTP-заголовки ───────────────
headers = {
    "Accept":
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Cache-Control": "max-age=0",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Sec-CH-UA": '"Google Chrome";v="135", "Not-A.Brand";v="8", "Chromium";v="135"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
    "Origin": "https://easytoyou.eu",
    "Referer": f"https://easytoyou.eu/decoder/{args.decoder}",
}

# список файлов, которые так и не удалось раскодировать
not_decoded: list[str] = []


# ─────────────── helpers ───────────────
def smart_sleep(base: int = WAIT_SEC):
    delay = max(5, base + random.uniform(-JITTER_SEC, JITTER_SEC))
    print(f"    ⏳ ждем {delay:0.1f} сек…", flush=True)
    time.sleep(delay)


def looks_encoded(head: bytes) -> bool:
    return any(m in head for m in IONCUBE_MARKERS)


def is_plain_php(name: str) -> bool:  # ← ДОБАВЛЕНО
    """
    Истинно только для файлов, у которых последним (единственным после
    последней точки) расширением является .php  —  например:
        ok  : foo.php, bar.test.php
        нет : foo.php.bak, foo.php.new, foo.php.txt
    """
    return name.lower().rsplit(".", 1)[-1] == "php"


# ─────────────── network ───────────────
def login(user: str, pwd: str) -> requests.Session | None:
    sess = requests.Session()
    data = {"loginname": user, "password": pwd}
    r = sess.post(f"{base_url}/login",
                  headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
                  data=data, allow_redirects=True, timeout=TIMEOUT_POST)
    if "/account" in r.url:
        print("✓ Успешный вход на easytoyou.eu")
        return sess
    print("✗ Ошибка авторизации (проверьте логин/пароль)")
    return None


def clear_queue(sess: requests.Session):
    print("Очистка очереди декодера…", end="", flush=True)
    while True:
        r = sess.get(f"{base_url}/decoder/{args.decoder}/1", headers=headers, timeout=TIMEOUT_GET)
        soup = bs4.BeautifulSoup(r.content, "lxml")
        inputs = soup.find_all(attrs={"name": "file[]"})
        if not inputs:
            print()  # перевод строки
            return
        data = "&".join(urllib.parse.urlencode({i["name"]: i["value"]}) for i in inputs)
        sess.post(f"{base_url}/decoder/{args.decoder}/1",
                  headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
                  data=data, timeout=TIMEOUT_POST)
        print(".", end="", flush=True)


def decode_response(data: bytes, enc: str) -> str:
    try:
        if enc == "zstd" and data.startswith(b"\x28\xb5\x2f\xfd"):
            return zstd.decompress(data).decode("utf-8", "replace")
        if enc == "gzip" and data.startswith(b"\x1f\x8b"):
            return gzip.decompress(data).decode("utf-8", "replace")
        if enc == "br":
            return brotli.decompress(data).decode("utf-8", "replace")
        return data.decode("utf-8", "replace")
    except Exception as e:
        return f"<!-- decode-error: {e} -->"


def parse_upload_result(resp: requests.Response):
    soup = bs4.BeautifulSoup(resp.content, "lxml")
    ok = [t.text.split()[1] for t in soup.select("div.alert-success")]
    bad = [t.text.split()[3] for t in soup.select("div.alert-danger")]
    return ok, bad


def upload_batch(sess: requests.Session, root: str, files: list[str]):
    """Отправляет batch .php, обрабатывает 429 и отсутствие формы."""
    url = f"{base_url}/decoder/{args.decoder}"

    for attempt in range(1, MAX_RETRY + 1):
        print(f"  ├─ попытка {attempt}/{MAX_RETRY}: получаю форму…", end="")
        try:
            r = sess.get(url, headers=headers, timeout=TIMEOUT_GET)
        except Exception as e:
            print(f" сет.ошибка: {e}")
            smart_sleep()
            continue

        if r.status_code == 429:
            print("  429")
            smart_sleep()
            continue
        print(" ok")

        soup = bs4.BeautifulSoup(
            decode_response(r.content, r.headers.get("Content-Encoding", "")),
            "lxml"
        )
        inp = soup.find("input", {"type": "file"})
        if not inp:
            print("  ✗ форма не найдена → повтор")
            if attempt < MAX_RETRY:
                smart_sleep()
                continue  # ⬅ повторяем заново GET-форму
            print("  ✗ превышен лимит попыток (форма)")
            return None
        field_name = inp["name"]

        # ─ формируем multipart ─
        handles, multipart = [], []
        for fn in files:
            fh = open(os.path.join(root, fn), "rb")
            handles.append(fh)
            multipart.append((field_name, (fn, fh, "application/x-php")))
        multipart.append(("submit", (None, "Decode")))

        print(f"  ├─ загружаю {len(files)} файл(ов)…", end="")
        try:
            r = sess.post(
                url,
                headers={**headers, "Referer": url, "Origin": "https://easytoyou.eu"},
                files=multipart,
                timeout=TIMEOUT_POST,
            )
        finally:
            for fh in handles:
                fh.close()

        if r.status_code == 429:
            print("  429")
            smart_sleep()
            continue

        ok, bad = parse_upload_result(r)
        print(f" ok ({len(ok)} успешно / {len(bad)} ошибок)")
        return ok, bad

    print("  ✗ превышен лимит попыток загрузки")
    return None


def download_zip(sess: requests.Session, out_dir: str) -> bool:
    for attempt in range(1, MAX_RETRY + 1):
        try:
            r = sess.get(f"{base_url}/download.php?id=all", headers=headers, timeout=TIMEOUT_DOWN)
        except Exception as e:
            print(f"  ├─ ошибка скачивания: {e}")
            smart_sleep()
            continue
        if r.status_code == 429:
            print("  ├─ 429 на скачивании")
            smart_sleep()
            continue
        try:
            with zipfile.ZipFile(BytesIO(r.content)) as zf:
                for name in zf.namelist():
                    data = zf.read(name)
                    dst = os.path.join(out_dir, os.path.basename(name))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with open(dst, "wb") as f:
                        f.write(data)
                    print(f"  └─ сохранён {dst} ({len(data)} байт)")
            return True
        except zipfile.BadZipFile:
            print("  ├─ ответ не ZIP (скорее всего декодер вернул ошибку)")
            return False
        except Exception as e:
            print(f"  ├─ ошибка распаковки: {e}")
            smart_sleep()
    print("  ✗ превышен лимит попыток скачивания")
    return False


# ─────────────── основной цикл ───────────────
def process_batch(sess: requests.Session, root: str, dst: str, batch_files: list[str]):
    print(f" → декодирую {len(batch_files)} файл(ов)…")
    rez = upload_batch(sess, root, batch_files)
    if rez is None:
        not_decoded.extend(os.path.join(root, f) for f in batch_files)
        return
    ok, bad = rez
    not_decoded.extend(os.path.join(root, f) for f in bad)
    if ok and not download_zip(sess, dst):
        not_decoded.extend(os.path.join(root, f) for f in batch_files)
    clear_queue(sess)


def main():
    if not args.destination:
        args.destination = os.path.basename(args.source.rstrip("/\\")) + "_decoded"

    sess = login(args.username, args.password)
    if not sess:
        return
    clear_queue(sess)

    for root, _, files in os.walk(args.source):
        rel = os.path.relpath(root, args.source)
        dst_dir = os.path.join(args.destination, rel) if rel != "." else args.destination
        os.makedirs(dst_dir, exist_ok=True)

        print(f"\n📂 Папка: {root}")
        php_files, other_files = [], []
        for fname in files:
            path = os.path.join(root, fname)
            try:
                with open(path, "rb") as fh:
                    head = fh.read(4096)
            except Exception as e:
                print(f"   └─ пропускаю {fname}: ошибка чтения ({e})")
                continue
            if is_plain_php(fname) and looks_encoded(head):
                php_files.append(fname)
            else:
                other_files.append(fname)

        for f in other_files:
            shutil.copy2(os.path.join(root, f), os.path.join(dst_dir, f))
            print(f"   └─ копирую без декода: {f}")

        if not php_files:
            continue

        if not args.overwrite:
            php_files = [f for f in php_files if not os.path.exists(os.path.join(dst_dir, f))]
        if not php_files:
            continue

        for i in range(0, len(php_files), BATCH_SIZE):
            process_batch(sess, root, dst_dir, php_files[i:i + BATCH_SIZE])

    if not_decoded:
        print("\n✗ Не удалось декодировать следующие файлы:")
        for p in not_decoded:
            print("  ", p)
    else:
        print("\n✓ Все ionCube-файлы успешно декодированы.")


if __name__ == "__main__":
    main()
