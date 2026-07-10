import logging
import os
from dotenv import load_dotenv
from github.GithubException import GithubException
from requests.exceptions import RequestException
from src.agent.graph import BugfixAgent



def main():
    # We load all the env variables (API KEYS) into the environment
    load_dotenv()

    
    graph = BugfixAgent()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger(__name__)


    try:
        graph.run()
    except KeyboardInterrupt:
        print("Exiting...")
        os._exit(0)

    except KeyError as e:
        print(f"Missing environment variable: {e}")
        os._exit(1)

    except RequestException as e:
        print(f"Network error: {e}")
        os._exit(1)

    except GithubException as e:
        print(f"Github API error: {e}")
        os._exit(1)
        
    except Exception as e:
        print(f"Error: {e}")
        os._exit(1)
    






if __name__ == "__main__":
    main()
    