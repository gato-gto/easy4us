# easy4us — автодекодер ionCube-скриптов через easytoyou.eu

> **TL;DR**: загружаете на VPS/PС проект с закрытыми **ionCube**-файлами, запускаете  
> `python main.py -u user -p pass -s ./src -o ./dst` — получаете раскодированную копию.  
> Работает на Linux / macOS / Windows, проверено на Debian 12, Ubuntu 22.04 и WSL.

---

## 🎯 Возможности

* 💾 массовая отправка файлов на декодер *easytoyou.eu*  
  (батчами по 25 шт., очередь чистится автоматически);
* 🔄 задержки и повторы (до 5) при 429 *Too Many Requests* или сетевых ошибках;
* 🏷️ распознаёт ionCube v10 / v11 по сигнатурам, игнорирует «обычные» `.php`;
* 🗄️ копирует **все** нефайлы `.php`, чтобы итоговая копия проекта была рабочей;
* 👀 подробный лог процесса — видно, что куда отправилось и где лежит результат;
* 🔒 не хранит пароль, использует единственную авторизованную сессию.

---

## ⚙️ Установка

```bash
git clone https://github.com/you/easy4us.git
cd easy4us
python -m venv venv      # по желанию
source venv/bin/activate # Linux/macOS
venv\Scripts\activate    # Windows

pip install -r requirements.txt
```

<details>
<summary>Если <code>pip install -r requirements.txt</code> падает ➜</summary>

1. Обновите основные инструменты сборки:

   ```bash
   python -m pip install --upgrade pip wheel setuptools
   ```

2. Поставьте dev-пакеты с заголовочными файлами для **brotli** и **zstd**.

   *Debian / Ubuntu*  
   ```bash
   sudo apt update
   sudo apt install -y build-essential python3-dev libbrotli-dev zstd
   ```

   *Fedora / RHEL*  
   ```bash
   sudo dnf install -y gcc python3-devel brotli-devel libzstd-devel
   ```

   *Alpine*  
   ```bash
   apk add --no-cache build-base python3-dev brotli-dev zstd-dev
   ```

3. Повторите установку:

   ```bash
   pip install -r requirements.txt
   ```

</details>

---

## 🚀 Запуск

```bash
python main.py \
  -u YOUR_LOGIN            \
  -p YOUR_PASS             \
  -s /path/to/encoded_src  \
  -o /path/to/decoded_dst  \
  -d ic11php74             \
  --overwrite              # (необязательно) перезаписывать существующие
```

Параметры | Описание
--------- | -----------------
`-u, --username` | логин на easytoyou.eu
`-p, --password` | пароль
`-s, --source`   | исходная папка с закодированным проектом
`-o, --destination` | куда писать раскодированные файлы (по умолчанию `<SRC>_decoded`)
`-d, --decoder` | нужный декодер на сайте (по умолчанию `ic11php72`, для PHP 7.4 используйте `ic11php74`)
`-w, --overwrite` | перезаписывать уже существующие файлы в папке назначения

---

## 💡 Как это работает

1. Скрипт логинится на *easytoyou.eu* и держит одну сессию.
2. Обходит каталог `source`, отделяя **закодированные** *.php* (по сигнатуре)  
   от остальных файлов.
3. Каждые 25 *.php* загружает формой декодера; ждёт, пока сервер положит результат в ZIP.
4. Скачивает архив, распаковывает файлы в ту же структуру, что и исходник.
5. Повторяет, пока не обработает всё дерево; ошибки и 429 — с повторами и джиттером.
6. В конце выводит список файлов, которые так и не удалось раскодировать.

---

## 📝 Лицензия

Проект распространяется под лицензией MIT. Используйте на свой страх и риск.  
*easytoyou.eu* — сторонний сервис; автор скрипта не связан с ним и не несёт ответственность за его работу.

---

> ⭐ Надеюсь, скрипт сэкономит вам кучу времени. PR и issues приветствуются!
