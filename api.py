from fastapi import FastAPI
from core.db_manager import DBManager
from bson import ObjectId

app = FastAPI()
db = DBManager()


@app.get("/tasks")
def get_all_tasks():
    # Fetch all tasks and convert ObjectId to string for JSON compatibility
    tasks = list(db.collection.find().sort("_id", -1))
    for t in tasks:
        t["_id"] = str(t["_id"])
    return tasks


@app.post("/run-pipeline")
async def trigger_pipeline():
    # This will trigger the main logic we've built
    import subprocess

    # We run the main.py as a separate process so the UI doesn't freeze
    subprocess.Popen(["python", "main.py"])
    return {"message": "Pipeline started!"}
