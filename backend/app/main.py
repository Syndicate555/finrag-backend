from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import chat, documents, reset, sections


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="Financial Document RAG API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(reset.router)
app.include_router(sections.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
