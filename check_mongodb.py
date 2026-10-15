from pymongo import MongoClient

client = MongoClient(
    "mongodb://127.0.0.1:27017",
    serverSelectionTimeoutMS=5000
)

try:
    client.admin.command("ping")
    print("SUCCESS: Python connected to MongoDB!")
    print("Database server is responding.")
except Exception as error:
    print("CONNECTION FAILED:")
    print(error)
finally:
    client.close()