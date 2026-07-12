import logging
import os
from src.utils.config import load_settings
from github.GithubException import GithubException
from requests.exceptions import RequestException
from src.agent.graph import SherpaAgent
import traceback
from src.tools.github import create_github_client
from src.agent.state import BugFixingState
from langchain_core.messages import HumanMessage

def main():
    # We load all the env variables (API KEYS) into the environment

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger(__name__)


    try:
        settings = load_settings()
        github_client =  create_github_client(token = settings.github_token)
        graph = SherpaAgent(settings = settings, github_client = github_client)
       
        state = BugFixingState(github_query=settings.github_queries, language = settings.github_language, labels = settings.github_labels, max_results=settings.github_max_results,     messages=[
        HumanMessage(
            content=(
                "Avvia la fase Discovery e trova issue "
                "candidate usando i tool disponibili."
            )
        )
    ])
        graph.run(state)
    except KeyboardInterrupt:
        print("Exiting...")
        os._exit(0)

    except KeyError as e:
        print(f"Missing environment variable: {e}")
        traceback.print_exc()
        os._exit(1)

    except RequestException as e:
        print(f"Network error: {e}")
        traceback.print_exc()
        os._exit(1)

    except GithubException as e:
        print(f"Github API error: {e}")
        traceback.print_exc()
        os._exit(1)
        
    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()
        os._exit(1)
    






if __name__ == "__main__":
    main()
    