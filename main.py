from fastapi import FastAPI, Depends, HTTPException
import models
from database import engine, SessionLocal
from typing import Annotated
from sqlalchemy.orm import Session
from starlette import status
from pydantic import BaseModel, Field

app = FastAPI(title="Todo API", description="A simple todo API", version="0.0.1")

models.Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class TodoRequest(BaseModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1, max_length=100)
    priority: int = Field(gt=0, lt=6)
    isCompleted: bool = False

    model_config = {
        "json_schema_extra": {
            "example": {
                "title": "Cleaning",
                "description": "Take the trash out",
                "priority": 1,
                "isCompleted": False,
            }
        }
    }


db_client = Annotated[Session, Depends(get_db)]


@app.get("/todos", status_code=status.HTTP_200_OK)
async def get_todos(db: db_client):
    return db.query(models.Todos).all()


@app.get("/todos/{todo_id}", status_code=status.HTTP_200_OK)
async def get_todo(todo_id: int, db: db_client):
    todo = db.query(models.Todos).filter(models.Todos.id == todo_id).first()
    if todo is not None:
        return todo
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Todo with id {} not found".format(todo_id),
    )


@app.post("/todos", status_code=status.HTTP_201_CREATED)
async def create_todo(todo: TodoRequest, db: db_client):
    todo = models.Todos(**todo.model_dump())
    db.add(todo)
    db.commit()


@app.put("/todos/{todo_id}", status_code=status.HTTP_200_OK)
def update_todo(todo_id: int, todo: TodoRequest, db: db_client):
    todo = models.Todos(**todo.model_dump())
    db.update(todo)
