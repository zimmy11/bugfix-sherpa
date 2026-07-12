from langchain_google_genai import ChatGoogleGenerativeAI
from src.utils.config import Settings 
from dataclasses import dataclass

@dataclass
class LLMNodesConfig:
    discovery: ChatGoogleGenerativeAI
    triage: ChatGoogleGenerativeAI
    ingestion: ChatGoogleGenerativeAI
    investigation: ChatGoogleGenerativeAI
    advisory: ChatGoogleGenerativeAI

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
    ) -> "LLMNodesConfig":
        return cls(
            discovery=ChatGoogleGenerativeAI(
                model=settings.discovery_model
            ),
            triage=ChatGoogleGenerativeAI(
                model=settings.triage_model
            ),
            ingestion=ChatGoogleGenerativeAI(
                model=settings.ingestion_model
            ),
            investigation=ChatGoogleGenerativeAI(
                model=settings.investigation_model
            ),
            advisory=ChatGoogleGenerativeAI(
                model=settings.advisory_model
            ),
        )