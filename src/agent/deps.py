from __future__ import annotations
from dataclasses import dataclass
import os
from langchain_google_genai import ChatGoogleGenerativeAI

@dataclass
class LLMNodesConfig:
    discovery: ChatGoogleGenerativeAI
    ingestion: ChatGoogleGenerativeAI 
    advisory: ChatGoogleGenerativeAI

    @staticmethod
    def load_llms_from_env() -> LLMNodesConfig:
        return LLMNodesConfig(
            discovery=ChatGoogleGenerativeAI(model=os.getenv("DISCOVERY_MODEL", "gemini-3.1-lite")),
            ingestion=ChatGoogleGenerativeAI(model=os.getenv("INGESTION_MODEL", "gemini-3.1-lite")),
            advisory=ChatGoogleGenerativeAI(model=os.getenv("ADVISORY_MODEL", "gemini-3.1-lite"))
        )



