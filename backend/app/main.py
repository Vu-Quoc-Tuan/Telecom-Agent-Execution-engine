from fastapi import FastAPI

app = FastAPI(title="Telecom Agent Execution Engine Backend")

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Telecom Agent Execution Engine API is running"}
