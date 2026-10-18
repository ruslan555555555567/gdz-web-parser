import hashlib
import secrets

password = "Ruslan#55"
salt = secrets.token_hex(16)
h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
final_hash = f"{salt}${h.hex()}"

print("ВСТАВЬ ЭТО В ONREZA:")
print(f"INSERT INTO settings (key, value) VALUES ('admin_password_hash', '{final_hash}') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;")