# check_redis.py
import redis
try:
    r = redis.Redis(host='localhost', port=6379, db=0)
    r.ping()
    print("✅ Redis работает и слушает порт 6379!")
except redis.ConnectionError:
    print("❌ Redis не запущен. Открой папку C:\\Redis и запусти redis-server.exe")