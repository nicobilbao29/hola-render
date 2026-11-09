"""Ejecutar una vez en tu computador: python configurar.py."""
from getpass import getpass
from pathlib import Path
import secrets
from dotenv import dotenv_values, set_key
from werkzeug.security import generate_password_hash

path = Path(__file__).with_name('.env')
existing = dotenv_values(path)
if not existing.get('SECRET_KEY'):
    set_key(str(path), 'SECRET_KEY', secrets.token_hex(32))
password = getpass('Crea tu contraseña para administrar (mínimo 12 caracteres): ')
if len(password) < 12 or password != getpass('Repite la contraseña: '):
    raise SystemExit('Contraseñas distintas o demasiado cortas. Ejecuta nuevamente configurar.py.')
set_key(str(path), 'ADMIN_PASSWORD_HASH', generate_password_hash(password))
print('Acceso configurado. Ejecuta python app.py y abre http://127.0.0.1:5000/admin')
print('Para Render, configura SECRET_KEY y ADMIN_PASSWORD_HASH con los valores de .env. No subas .env a GitHub.')
