# Bugfix Sherpa — roadmap del lavoro rimanente

## Scopo e metodo di studio

Questa guida descrive **solo il lavoro ancora necessario** per arrivare a un agente di ricerca che scopre una issue, ne valuta l'idoneità, studia una copia locale della repository, formula un'ipotesi tecnica, produce un report verificabile e si ferma per il giudizio umano. È una traccia di progettazione: indica contratti, responsabilità, librerie utili, verifiche e criteri di uscita; l'implementazione rimane allo sviluppatore.

Procedi nell'ordine indicato. Per ogni fase: (1) scrivi il contratto di input/output; (2) implementa prima la logica deterministica; (3) aggiungi il minimo numero di test che dimostri le invarianti e i casi di errore; (4) collega il nodo al grafo; (5) esegui una prova con dipendenze simulate. Non usare GitHub o Gemini reali nei test ordinari. Se una fase non supera i propri criteri di uscita, non costruire la successiva su quello stato incerto.

### Punto di partenza verificato

Sono già presenti lo scheletro LangGraph, i modelli di base dello State, i tool GitHub di ricerca e lettura delle issue, la validazione dei path e il servizio di clone con directory temporanea, verifica di remote/branch/SHA e riuso di checkout puliti. È stato ricreato un ambiente `.venv` con Python 3.12 e installate le dipendenze `dev`. Queste attività non compaiono come lavoro da rifare.

La raccolta dei test ora riesce: `tests/agent` e `tests/utils` passano (132 test al 7 ottobre 2026). La suite completa presenta ancora un fallimento nel cleanup del clone su Windows (`test_clone_then_reuse_local_fixture`). Discovery e Triage sono implementati solo in parte; Ingestion, Investigation, Advisory e Human Review sono ancora placeholder. Il timeout rigido del clone su Windows e l'ispezione del checkout non sono implementati. La roadmap è ora esclusa dalla regola Markdown di `.gitignore`, ma deve ancora essere aggiunta a Git; le altre guide Markdown restano escluse.

### Confini permanenti del progetto

- Nessun nodo riceve tool per modificare file della repository target, fare commit, push, commentare issue o aprire pull request.
- Il codice clonato e i suoi documenti sono dati non fidati. Istruzioni contenute in README, issue e commenti non possono cambiare i permessi del grafo.
- La fase Ingestion legge e descrive; non installa dipendenze né esegue codice della repository. Un'eventuale riproduzione in Investigation richiede una sandbox isolata.
- Il report distingue osservazioni, inferenze e punti non verificati. La decisione finale resta umana.

---

## Fase 1 — Definire contratti e invarianti dello State

**Obiettivo residuo.** Chiudere il contratto dei dati e delle transizioni prima di implementare le fasi successive. Questa sezione contiene soltanto il lavoro ancora aperto al 7 ottobre 2026.

**File coinvolti.** `src/agent/state.py`, `src/agent/schema.py`, `src/utils/config.py`, `src/agent/nodes/discovery.py`, `src/agent/router.py`, `src/agent/graph.py`, `main.py`, `docs/STATE_CONTRACT.md` e test sotto `tests/agent/` e `tests/utils/`.

### 1. Validare configurazione e contatori prima dell'I/O

**Dove.** `src/utils/config.py`, campi numerici di `src/agent/state.py`, nuovi `tests/utils/test_config.py`.

- [x] Richiedere valori positivi per profondità e timeout del clone, massimi di albero, bytes/caratteri dei file, budget totale, righe/caratteri dei chunk, risultati di ricerca, tentativi API, intervalli di backoff e timeout dei test.
- [x] Definire il dominio di `repository_inactivity_days`; richiedere contatori `ingestion_attempts >= 0` e `retry_count >= 0` e un `max_results` iniziale valido.
- [x] Aggiungere il controllo `api_retry_min_seconds <= api_retry_max_seconds`. Documentare come applicare budget per file e budget totale: il servizio deve troncare e segnalare quando il totale è esaurito.
- [x] Correggere `github_queries: list[str] = None` con un tipo che ammetta esplicitamente `None`; rifiutare query esplicite vuote o di soli spazi.
- [x] Uniformare il nome `max_repository_tree_entries` in codice, guide e test.
- [x] Collegare in `load_settings()` i limiti configurabili tramite ambiente, validandoli prima di creare client o invocare tool.

**Verifiche.** Test parametrizzati per zero/negativi, backoff invertito, query vuote, contatori negativi e configurazione valida. Credenziali fittizie, nessuna rete.

**Dominio dell'inattività.** `repository_inactivity_days` è un intero maggiore di zero. Discovery considera attiva una repository se l'ultimo push è entro la finestra `now - repository_inactivity_days`; zero e valori negativi sono configurazioni invalide.

**Politica dei budget per Ingestion.** `max_repository_file_bytes` limita i byte letti da ogni file prima della decodifica. `max_guide_chars` limita i caratteri conservati per ogni guida; per i manifest il budget per file è quello dei byte e il contenuto conservato resta comunque soggetto al budget totale. `max_ingestion_total_chars` limita la somma dei caratteri di guide e manifest nello snapshot. L'ispezione deve procedere in ordine deterministico, conservare al massimo i caratteri ancora disponibili, segnare come troncato ogni file tagliato e aggiungere un warning quando un limite per file o totale esaurisce i dati disponibili. Una volta esaurito il totale, non deve leggere altri contenuti. L'implementazione e i test del servizio restano nella fase 4; questa fase definisce e verifica la configurazione.

**Stato delle verifiche.** `tests/utils/test_config.py` copre zero, valori negativi e il valore valido `1` per i 14 limiti sopra elencati e per `repository_inactivity_days`. Verifica inoltre valori personalizzati, invalidi e default dei dieci limiti caricati dall'ambiente, backoff invertito e query esplicite vuote. `tests/agent/test_state.py` verifica contatori e limite dei risultati.

### 2. Evitare duplicazioni dopo retry o ripresa

**Dove.** `src/agent/state.py`, `src/agent/nodes/discovery.py`, nuovi `tests/agent/test_state.py` e test offline Discovery.

- [ ] Rivedere i quattro reducer `operator.add` rimasti: `files_analyzed`, `search_results`, `relevant_symbols`, `error_signatures`. Scegliere per ciascuno sostituzione oppure accumulo con deduplicazione.
- [ ] Per la sostituzione, restituire la collezione completa aggiornata. Per l'accumulo, definire chiavi stabili: path; file/riga/query; file/simbolo/riga; firma normalizzata.
- [ ] Correggere Discovery, che ripercorre i vecchi `ToolMessage` e accoda nuovamente le candidate. Deduplicare per `(repository_full_name, issue_number)` e restituire una lista tipizzata entro il limite fidato.

**Verifiche.** Applicare due aggiornamenti identici attraverso un piccolo `StateGraph`: il secondo non deve duplicare i dati. Ripetere Discovery con gli stessi messaggi senza aumentare il numero di candidate.

### 3. Completare il contratto dello snapshot Ingestion

**Dove.** `src/agent/schema.py`, `src/agent/state.py`, test dei modelli e di `validate_ingestion_snapshot()`.

- [ ] Rappresentare esplicitamente l'esito dell'ispezione: `test_config_files is not None` non deve essere l'unico indicatore. Dopo aver introdotto il discriminante, allineare questo campo a una lista con `default_factory=list`.
- [ ] Aggiungere al risultato di ispezione l'identità del checkout analizzato, almeno path e SHA, e confrontarla con i metadati del clone. Definire dove conservare il branch effettivamente verificato.
- [ ] Rafforzare `validate_ingestion_snapshot()`: remote pubblico atteso, branch/SHA coerenti, contenimento del path nel workspace secondo Settings fidate. Stringhe presenti non bastano a provare uno snapshot valido.
- [ ] Definire i dati minimi per accettare un risultato `partial` e conservare i warning. Guide, manifest o albero vuoti possono essere legittimi: distinguere assenza di file, troncamento e ispezione non effettuata.
- [ ] Definire i vincoli dei path relativi esposti e dei budget di quantità/dimensioni dello snapshot.

**Confine della fase.** Qui si definiscono modelli e controlli della transizione. L'ispezione effettiva, le verifiche del checkout sul disco e la macchina a stati operativa restano nelle fasi 4 e 5; i soli modelli non provano tali verifiche.

**Verifiche.** Metadati mancanti, remote errato, SHA dell'ispezione diverso dal clone, path esterno, ispezione non effettuata, snapshot parziale ammissibile/non ammissibile e collezioni vuote legittime. Usare fixture locali o controlli sul disco simulati.

### 4. Completare provenienza delle evidenze e risultati intermedi

**Dove.** `src/agent/schema.py`, `src/agent/state.py`, `tests/agent/schemas/`.

- [ ] Dare a `InvestigationFinding` una provenienza discriminata: codice con path relativo e riga positiva, oppure log con riferimento all'esecuzione e un estratto limitato. Rifiutare finding con contenuto ma senza fonte.
- [ ] Rifiutare testi obbligatori di soli spazi e path non ammessi. La verifica dell'esistenza effettiva di file/righe rimane distinta dalla validazione di forma.
- [ ] Se si mantiene `confidence_score`, vincolarlo a `0–1` e richiedere una motivazione della conclusione complessiva, distinta dalle confidenze dei finding.
- [ ] Allineare i campi dei test nello State a `TestExecutionResult`, oppure introdurre una conversione validata dei campi separati. Impedire `tests_passed=True` con timeout o exit code non zero; il runner è la fonte del report.

**Verifiche.** Fonte mancante, evidenze codice/log valide, provenienza incompleta, path assoluto/traversal, testi di soli spazi, confidenza complessiva invalida e conversione incoerente del risultato dei test.

### 5. Definire errori strutturati e stati coerenti per il routing

**Dove.** `src/agent/schema.py`, `src/agent/state.py`, nodi, `src/agent/router.py` e `src/agent/graph.py`.

- [ ] Definire `WorkflowError` con `phase`, `category`, `message` sanitizzato e `retryable`; aggiungerlo allo State e usarlo nei percorsi di errore. Aggiornare i consumatori prima di rimuovere gli errori testuali precedenti.
- [ ] Distinguere errori operativi recuperabili, input invalidi e violazioni delle invarianti. Non ritentare indiscriminatamente le violazioni.
- [ ] Definire un esito Discovery tipizzato: completamento, nessun risultato ed errore. Il router non deve considerare completata qualsiasi risposta senza tool call.
- [ ] Gestire `triage_status="failed"` nel router e nella mappa del grafo. Documentare la relazione fra stato globale e stati di fase.
- [ ] Definire il dominio di feedback e `approval_status`, riferito al report. Interrupt e ripresa operativa restano nella fase 9.

**Verifiche.** Errore ritentabile/non ritentabile, Discovery senza risultati/in errore, Triage fallito e arresto prima della fase successiva dopo un fallimento.

### 6. Centralizzare l'inizializzazione

**Dove.** `src/agent/state.py` oppure modulo dedicato, `main.py`, `tests/agent/test_state.py`.

- [ ] Implementare `build_initial_state(request, settings)`: normalizzare e validare query, lingua, label e limite risultati, impostando i valori iniziali delle fasi.
- [ ] Usare la funzione in `main.py` al posto della costruzione manuale dello State.
- [ ] Conservare credenziali, workspace e limiti operativi nelle Settings fidate; richiesta e aggiornamenti LLM non devono poterli aumentare o sostituire.

**Verifiche.** State iniziale valido, input normalizzati, input invalidi rifiutati e credenziali assenti dal JSON dello State.

### 7. Consolidare test e tabella del contratto

**Dove.** `tests/agent/test_state.py`, `tests/utils/test_config.py`, test del routing e `docs/STATE_CONTRACT.md`.

- [ ] Aggiungere test automatici per State vuoto/popolato e round trip JSON con messaggi e modelli annidati. Le verifiche manuali precedenti non sostituiscono questi test.
- [ ] Aggiungere transizioni valide e casi mancanti: candidata estranea, branch incoerente e snapshot valido. Provare aggiornamenti parziali senza imporre invarianti globali che rifiutino stati intermedi legittimi.
- [ ] Collegare un piccolo grafo offline con nodi fake per dimostrare inizializzazione, passaggio dei modelli, routing e arresto su errore. Non richiede Investigation o Advisory completi.
- [ ] Aggiornare `docs/STATE_CONTRACT.md`: tipi, default, riproduzione dichiarata/verificata, report, campi Advisory rimossi, provenienza, errori e politiche di aggiornamento, conservando produttore e consumatore.

**Comando di verifica.** Dopo aver aggiunto i test di configurazione: `python -m pytest -q -p no:cacheprovider tests/agent tests/utils`. I test del clone riguardano le fasi successive.

**Criterio di uscita.** Input e limiti invalidi vengono rifiutati; aggiornamenti ripetuti non duplicano snapshot; le transizioni usano esiti tipizzati e invarianti; test offline e tabella del contratto dimostrano serializzazione, routing ed errori.

---

## Fase 2 — Stabilizzare Discovery e Triage

**Obiettivo.** Selezionare al massimo una issue realmente esistente e ancora adatta all'indagine, con decisione spiegabile. I tool GitHub esistono; il lavoro riguarda coerenza dei dati, limiti e test.

**File coinvolti.** `src/tools/github.py`, `src/agent/nodes/discovery.py`, `src/agent/nodes/triage.py`, `src/agent/router.py`, `src/prompts/discovery.py`, `src/prompts/triage.py`, test in `tests/tools/` e `tests/nodes/`.

### Discovery

1. Fai produrre al tool un risultato piccolo con `status`, `message` e candidate. Raccogli metriche essenziali della repository senza passare oggetti PyGithub allo State. Prima dell'LLM applica filtri deterministici: issue aperta, non assegnata, repository non archiviata e attiva, descrizione presente, label di esclusione e duplicati.
2. Il nodo attuale aggiunge a `issue_candidates` tutti i risultati `search_github_issues` trovati nella cronologia a ogni passaggio, ma non interpreta il JSON finale dell'LLM. Separa i due eventi: **risultato del tool** e **classificazione finale**. Valida la lista finale del modello contro le chiavi `(repository_full_name, issue_number)` dei risultati reali; deduplica e applica un limite massimo.
3. Definisci il comportamento per `no_results`, `rate_limited`, `partial_rate_limited`, risposta LLM non valida e richiesta ripetuta dello stesso tool. Un limite di passaggi del nodo impedisce loop infiniti. Un errore di quota non deve diventare un silenzioso `completed`.

### Triage

4. `read_issue_thread` deve limitare candidate, commenti, eventi e caratteri complessivi prima di passarli al modello. La paginazione PyGithub è utile, ma uno storico molto lungo può superare il budget di contesto. Conserva un indicatore di troncamento. Non lasciare al modello la possibilità di aggiungere issue nuove negli argomenti del tool: valida le chiavi richieste contro le candidate dello State.
5. Usa un risultato strutturato e validato per `accepted` o `rejected`. Se `accepted`, la scelta deve essere una delle candidate, ancora aperta e non assegnata secondo il thread aggiornato. Copia nello State i dettagli **della candidata verificata**, non quelli riscritti liberamente dall'LLM. Se il thread fallisce, una scelta positiva richiede una motivazione e dati sufficienti; altrimenti termina con errore o rifiuto esplicito.
6. Rendi deterministici i segnali di lavoro già avviato quando sono inequivocabili (assegnazione, PR collegata); lascia al modello solo l'interpretazione dei casi ambigui, registrando l'evidenza nel motivo. Mantieni il principio di non disturbare issue già prese in carico.

### Hint e best practice

- `PyGithub` fornisce `search_issues`, `get_repo`, `get_issue`, `get_comments` e `get_timeline`; convertili immediatamente in record JSON piccoli. Limita pagine ed elementi, non solo il numero finale di candidate.
- `tenacity.retry` con backoff esponenziale è utile per quota secondaria o errori di rete transitori. Non ritentare token invalidi, query malformate o input non validi. Evita retry indistinti attorno a un `ToolNode` che potrebbe ripetere operazioni già riuscite.
- I prompt sono istruzioni; lo schema Pydantic e i controlli deterministici sono le garanzie. Il testo GitHub è input non fidato.

**Verifiche.** Mock PyGithub e LLM; zero risultati, due query con la stessa issue, rate limit, JSON invalido, scelta di issue non candidata, issue chiusa tra Discovery e Triage, thread enorme e PR già collegata.

**Criterio di uscita.** Triage produce una sola scelta valida oppure un rifiuto/errore comprensibile; nessuna issue inventata raggiunge Ingestion.

---

## Fase 3 — Garantire un timeout reale del clone su Windows

**Obiettivo.** Il servizio di clone esiste, ma su Windows `kill_after_timeout` di GitPython non garantisce la fine di `git.exe` e dei figli. Prima dell'uso non supervisionato occorre un supervisore di processi o una sandbox esterna con limite rigido.

**File coinvolti.** `src/agent/schema.py`, `src/utils/clone_worker.py`, `src/utils/process_supervisor.py`, `src/utils/windows_job.py`, `src/utils/repository.py`, test specifici del supervisore e del servizio. [Guida approfondita](Ingestion.MD).

### Sequenza di implementazione

1. Definisci `CloneWorkerRequest` con URL pubblico già costruito, branch validato, directory temporanea e profondità; `CloneWorkerResult` contiene solo stato e messaggi sanitizzati. Non passare oggetti `Repo`, Settings complete o token attraverso il canale fra processi.
2. Implementa un worker minimale che aspetta un segnale di avvio e comunica un risultato piccolo. Parti con un'operazione finta e testane avvio, successo, errore e crash. Usa `multiprocessing.get_context("spawn")` su Windows; il target del worker deve stare al livello del modulo ed essere importabile dal processo figlio.
3. Crea il Job Object Windows con flag di terminazione dei processi alla chiusura. Associa il worker al job **prima** di autorizzare il clone. `pywin32` o le API Win32 tramite `ctypes` sono opzioni; scegli una sola e dichiarala nelle dipendenze. Gestisci esplicitamente il caso in cui l'associazione al job fallisce: non avviare il clone senza garanzia equivalente.
4. Il supervisore attende entro il limite, distingue risultato, uscita senza risultato e timeout, e termina l'intero job quando necessario. Solo dopo aver accertato la fine dei processi restituisce il controllo al servizio chiamante. `time.monotonic()` evita problemi con modifiche dell'orologio di sistema.
5. Integra GitPython nel worker. Il servizio già presente continua a possedere la directory temporanea, verifica remote/branch/SHA e pubblica il checkout finale soltanto dopo successo. In caso di errore, elimina **solo** la directory temporanea posseduta, dopo la conferma che nessun processo vi scrive più.
6. Se non puoi garantire il timeout in un ambiente Windows specifico, fallisci chiuso o richiedi esplicitamente una sandbox esterna. Non documentare un timeout apparente come se fosse una garanzia.

### Contratti suggeriti

| Funzione | Responsabilità |
| --- | --- |
| `run_clone_worker(request, channel, start_event)` | Aspettare il via, eseguire il solo clone e inviare un risultato serializzabile. |
| `start_clone_worker(request)` | Creare processo, canale ed Event; associarli al Job Object prima del via. |
| `wait_for_clone_worker(handle, timeout_seconds)` | Distinguere successo, errore Git, crash e timeout senza attendere indefinitamente. |
| `terminate_clone_process_tree(handle)` | Terminare worker e discendenti, attendere la conclusione e chiudere gli handle. |
| `clone_into_temporary_directory(...)` | Usare il supervisore; non scegliere il path finale né fare cleanup di directory non possedute. |

**Verifiche.** Worker finto che termina normalmente, worker che resta bloccato, figlio che resta vivo dopo il padre, errore Git sanitizzato, checkout finale assente dopo timeout, checkout preesistente intatto, directory esterna intatta. Testa con una repository Git locale; nessuna rete necessaria.

**Criterio di uscita.** Un test automatico prova che dopo il timeout non restano `git.exe` o figli attivi e il clone finale non viene pubblicato.

---

## Fase 4 — Ispezionare il checkout senza eseguirlo

**Obiettivo.** Trasformare un clone verificato in uno snapshot limitato, serializzabile e utile per l'indagine. L'ispezione è deterministica: l'LLM non sceglie path arbitrari, limiti o esclusioni. [Guida approfondita](docs/INGESTION_IMPLEMENTATION_GUIDE.md).

**File coinvolti.** `src/utils/repository.py` oppure un modulo `repository_inspection.py` dedicato, `src/agent/schema.py`, `src/utils/config.py`, `src/tools/repository.py`, test offline in `tests/tools/`.

### Funzioni e responsabilità

| Funzione | Cosa deve fare | Hint |
| --- | --- | --- |
| `map_repository_tree(root, max_depth, max_entries)` | Restituire path relativi POSIX, ordinati, esclusioni applicate e flag `truncated`. | `pathlib.Path` e `os.scandir`; non seguire symlink o junction. |
| `is_sensitive_file(relative_path)` | Escludere `.env`, chiavi, credenziali e pattern configurati sia da albero esposto sia da letture. | `fnmatch` sui segmenti del path, con attenzione al case su Windows. |
| `read_bounded_text_file(root, path, max_bytes, max_chars)` | Verificare contenimento e tipo, rifiutare binari, leggere entro budget, indicare encoding e troncamento. | `Path.resolve`, `stat`, lettura binaria limitata, decodifica UTF-8 e fallback controllato. |
| `find_project_guides(root)` | Trovare README, CONTRIBUTING, DEVELOPMENT, TESTING e posizioni esplicitamente ammesse. | Globbing ristretto e ordinamento stabile. |
| `find_project_manifests(root)` | Trovare pyproject, setup, requirements, lock file e config di test. | Non scansionare vendor/build. |
| `read_project_guides(...)` / `read_project_manifests(...)` | Applicare limiti per file e un budget totale; restituire contenuti e warning. | Una guida assente è un warning, non sempre un errore fatale. |
| `detect_project_language(tree, manifests)` | Deducere il linguaggio da segnali locali; segnalare ambiguità. | Privilegiare manifest, poi estensioni. |
| `detect_package_manager(manifests)` | Rilevare uv, Poetry, Pipenv o pip dai file già letti. | Funzione pura; non suggerire comandi da eseguire. |
| `extract_python_version_constraint(manifests)` | Leggere `requires-python` e versioni dichiarate. | `tomllib` per TOML, non regex sul file intero. |
| `detect_test_framework(manifests, tree)` | Rilevare pytest/unittest e file di configurazione. | Non eseguire test. |
| `inspect_repository_service(path, settings)` | Orchestrare verifiche e funzioni sopra, restituire `completed`, `partial` o `failed`. | Convertire eccezioni operative in risultati strutturati. |

### Invarianti di sicurezza

- Verifica che il path del checkout sia dentro `workspace_root`, corrisponda a un checkout Git valido e non sia stato sostituito fra clone e ispezione. Prima di leggere, confronta il suo remote e SHA con i valori acquisiti dal clone.
- Non seguire symlink o junction fuori dal checkout. Controlla anche il singolo file immediatamente prima della lettura: una scansione precedente non basta se il filesystem cambia.
- Non leggere `setup.py` eseguendolo: per Ingestion è solo testo non fidato. Non fare `pip install`, `pytest`, import di moduli della repository o hook Git.
- Budget distinti per bytes, caratteri, numero di documenti e totale passato al modello. Restituisci warning quando un limite tronca l'informazione.

### Esporre il tool

In `build_repository_tools(settings)` aggiungi `inspect_cloned_repository(local_repo_path)`. La factory chiude Settings e limiti; il tool restituisce `InspectRepositoryResult.model_dump(mode="json")`. Registralo nel gruppo `ingestion` accanto a `clone_selected_repository`. La docstring deve spiegare quando usarlo e che cosa non fa.

**Verifiche.** Checkout vuoto, senza README, più manifest, encoding differente, binario, file grande, `.env`, symlink/junction esterno, albero oltre profondità o numero massimo, budget totale esaurito, TOML malformato e output JSON serializzabile.

**Criterio di uscita.** Il tool produce uno snapshot limitato e ripetibile, senza leggere file sensibili né eseguire codice della repository.

---

## Fase 5 — Completare la macchina a stati Ingestion

**Obiettivo.** Collegare clone e ispezione al grafo senza dichiarare successo sulla base di una frase del modello.

**File coinvolti.** `src/agent/nodes/ingestion.py`, `src/agent/router.py`, `src/agent/graph.py`, `src/tools/__init__.py`, `src/prompts/ingestion.py`, test del nodo, router e sottografo.

### Sequenza richiesta

1. In `pending`, verifica che Triage abbia accettato una issue e che nome repository, numero, branch e candidata selezionata siano coerenti. Predisponi una sola richiesta di clone.
2. In `cloning`, cerca il risultato più recente **del tool clone** nella cronologia. Se manca, non avanzare. Se fallisce, salva categoria e motivo e vai a `failed`. Se riesce, valida repository, path nel workspace, remote, branch e SHA; salva i metadati e predisponi una sola richiesta di ispezione.
3. In `inspecting`, accetta solo il risultato **del tool inspect** per quel checkout e SHA. Valida dimensioni, tipi e limiti dello snapshot. In caso di `partial` conserva warning; passa a `completed` solo se i dati minimi sono presenti. Un risultato `failed` non cancella i metadati del clone.
4. In `completed`, non richiamare tool. In `failed`, non tentare workaround inventati dal modello; il router va a una terminazione comprensibile o a un report di fallimento.
5. Alla ripresa dopo checkpoint, deriva la fase dallo State e dai `ToolMessage` persistiti. Una stessa risposta ricevuta due volte non deve clonare di nuovo né duplicare albero, guide e manifest. Limita i tentativi complessivi.

### Helper da progettare

`_message_content` interpreta JSON del tool con errore controllato; `_latest_tool_result` seleziona per nome e identificativo della chiamata; `_validate_ingestion_preconditions`, `_validate_clone_result` e `_validate_inspection_result` difendono i confini; `_apply_clone_result` e `_apply_inspection_result` producono **aggiornamenti parziali** dello State. Mantieni gli helper puri quando possibile.

### Routing e grafo

`route_after_ingestion` può restituire `tools` solo per una tool call ammessa, `completed` solo con snapshot valido, `failed` per errore esplicito. Ogni altro stato è incoerente e va reso visibile, non trattato come successo. Nel grafo aggiungi il ramo `failed`; lega all'LLM Ingestion soltanto i due tool della fase. Valuta se sostituire l'orchestrazione LLM con un nodo interamente deterministico: il modello non aggiunge valore alla scelta dell'URL, dei limiti o dell'ordine clone→inspect.

**Verifiche.** Precondizioni mancanti, clone corretto, clone fallito, repository diversa, path esterno, inspect completo/parziale/fallito, messaggio JSON invalido, duplicazione del `ToolMessage`, ripresa dopo clone e dopo inspect, router che rifiuta `completed` senza SHA. Usa fake tool e fake LLM, mai rete.

**Criterio di uscita.** Il sottografo offline arriva a Investigation soltanto con snapshot valido; tutti i fallimenti terminano senza proseguire.

---

## Fase 6 — Costruire i tool di lettura per Investigation

**Obiettivo.** Consentire ricerca mirata nel checkout senza inviare l'intera codebase al modello. `fs_read_file` attuale legge file interi da path forniti dal chiamante e va sostituito prima di renderlo disponibile a Investigation.

**File coinvolti.** `src/tools/fs_read.py`, nuovi `src/tools/code_search.py` e `src/tools/ast_inspect.py`, test dedicati.

| Tool/funzione | Responsabilità | Hint di implementazione |
| --- | --- | --- |
| `resolve_repository_file(root, relative_path)` | Accettare soltanto file regolari nel checkout, non sensibili, senza attraversare link esterni. | Riusa le regole di `repository_safety`; rifiuta path assoluti e `..`. |
| `read_file_chunk(path, start_line, end_line, max_chars)` | Restituire righe numerate e indicazione di troncamento. | `enumerate`, limiti di righe e caratteri imposti dalle Settings. |
| `search_codebase(root, pattern, file_glob, max_results)` | Cercare occorrenze con file, riga ed estratto limitato. | `ripgrep` come processo senza shell e con timeout, oppure scansione Python controllata; valida pattern/glob. |
| `list_python_symbols(path)` | Elencare classi e funzioni con riga e firma sintetica. | `ast.parse`, `ast.walk` e `lineno`; sintassi invalida è un risultato, non un crash. |
| `get_function_signature(path, symbol)` | Restituire la sola firma e posizione del simbolo richiesto. | Non importare il modulo della repository: il parsing statico è sufficiente. |

Esponi solo path relativi al checkout già selezionato. Il modello non può passare una nuova root o aumentare i limiti. Un risultato di ricerca deve includere provenienza esatta; un estratto privo di file e riga non è una buona evidenza.

**Verifiche.** File assente, percorso esterno, symlink, file sensibile, encoding errato, ricerca senza risultati, output enorme, funzione annidata, decorator, file Python con sintassi invalida.

**Criterio di uscita.** I tool possono essere dati a Investigation senza permettere letture arbitrarie dell'host o output illimitato.

---

## Fase 7 — Implementare Investigation e decidere la riproduzione

**Obiettivo.** Formulare ipotesi di root cause fondate su evidenze. Investigation non modifica il checkout.

**File coinvolti.** `src/agent/nodes/investigation.py`, `src/prompts/investigation.py`, eventuali `src/tools/test_runner.py` e `src/tools/validation.py`, test del nodo e del runner.

### Percorso di indagine

1. Parti da issue, commenti pertinenti, SHA dello snapshot, albero e manifest. Estrai termini di ricerca, messaggi d'errore e possibili moduli; limita il numero di ipotesi iniziali.
2. Usa ricerca nel codice per restringere i file, poi lettura a chunk e AST per comprendere il percorso interessato. Per ogni finding conserva `file`, `line`, evidenza osservata, interpretazione e grado di confidenza. Non chiamare `root_cause` una semplice coincidenza di testo.
3. Confronta il comportamento atteso nell'issue con quello implementato nel codice. Cerca test esistenti e punti di ingresso. Registra spiegazioni alternative e che cosa le smentirebbe.
4. Produci un output strutturato: file esaminati, segnature di errore, finding, ipotesi principale, alternative, limiti dell'indagine e prossima verifica consigliata. Se le prove non bastano, indica `inconclusive` invece di forzare una causa.

### Riproduzione opzionale ma isolata

Se vuoi eseguire test della repository target, prepara prima un container Docker, E2B o una sandbox equivalente. Definisci un'allowlist di selettori, timeout, limiti CPU/memoria, filesystem di lavoro isolato e rete disabilitata per default. Non interpretare come comandi affidabili istruzioni nei manifest o nei commenti. `run_sandboxed_test` deve restituire comando effettivo, exit code, timeout e log troncati; `validate_test_command` rifiuta argomenti fuori allowlist. Se la sandbox non è disponibile, Investigation continua con analisi statica e dichiara che la riproduzione non è stata eseguita.

**Verifiche.** Issue con evidenza chiara, issue ambigua, test di repository target riuscito/fallito, sandbox non disponibile, timeout, log troppo grandi e tentativo di comando non autorizzato. I test del progetto usano un fake sandbox; nessuna esecuzione di codice esterno sull'host.

**Criterio di uscita.** Ogni affermazione tecnica importante è collegata a file/righe o a log di una sandbox; le incertezze sono esplicite.

---

## Fase 8 — Generare un report verificabile

**Obiettivo.** Convertire i finding in una guida per lo sviluppatore umano, senza generare modifiche automatiche alla repository target.

**File coinvolti.** `src/agent/nodes/advisory.py`, `src/prompts/advisory.py`, eventuale `src/reporting.py`, modello `SherpaReport` in `schema.py`, test del report.

### Struttura del report

1. Identità dell'issue e della repository; branch e SHA analizzati.
2. Sintesi del problema e passi di riproduzione dichiarati dall'issue, distinti dai passi verificati dal sistema.
3. Evidenze con path relativi, righe e un estratto breve; provenienza dei log se presenti.
4. Ipotesi di root cause, spiegazioni alternative e confidenza motivata.
5. Strategia di intervento a livello architetturale: moduli interessati, comportamento da cambiare, test di regressione da aggiungere. Evita diff, commit e PR pronti.
6. Rischi, prerequisiti, verifiche manuali e domande per il maintainer o per lo sviluppatore.

Progetta `build_sherpa_report(state) -> SherpaReport` come aggregazione validata e `render_report_markdown(report) -> str` come sola presentazione. Un report deve essere leggibile anche senza risposta LLM perfetta: i campi obbligatori e gli errori restano deterministici. Non dichiarare test superati quando `tests_passed` è assente.

**Verifiche.** Finding completi, indagine inconcludente, snapshot parziale, test non eseguiti, test falliti, riferimenti a file inesistenti e report che non espone segreti o contenuti lunghi.

**Criterio di uscita.** Un umano può distinguere fatti, ipotesi e lavoro da svolgere senza leggere la conversazione interna del grafo.

---

## Fase 9 — Rendere reale la pausa Human-in-the-Loop

**Obiettivo.** Mostrare il report e sospendere l'esecuzione in modo riprendibile. Il placeholder `human_review_node` e il tool `ask_human` esistente non bastano da soli: la pausa obbligatoria non deve dipendere dalla volontà dell'LLM.

**File coinvolti.** `src/agent/nodes/human_review.py`, `src/agent/graph.py`, `src/agent/router.py`, `main.py`, test del grafo.

1. Il nodo Human Review costruisce una richiesta con report, incertezze e decisione attesa e chiama `langgraph.types.interrupt` in modo deterministico.
2. Compila il grafo con un checkpointer. Per le prove locali `MemorySaver` è sufficiente; per riprese oltre il processo scegli uno storage persistente supportato. Ogni esecuzione usa un `thread_id` stabile e distinto.
3. Definisci il formato del feedback: accettazione del report, richiesta di approfondimento o correzione di dati. Convalida il valore alla ripresa; non interpretare un testo libero come autorizzazione a scrivere codice o pubblicare una PR.
4. Se il report manca per un errore precedente, mostra un esito di fallimento comprensibile anziché un interrupt che chiede di approvare informazioni inesistenti.

**Verifiche.** Il grafo si sospende davvero, conserva lo State, riprende sullo stesso `thread_id`, registra il feedback una sola volta e termina senza tool di scrittura.

**Criterio di uscita.** La pausa è osservabile e testata in un'esecuzione end-to-end offline.

---

## Fase 10 — CLI, osservabilità e documentazione d'uso

**Obiettivo.** Permettere a uno sviluppatore di avviare il flusso, capirne l'esito e riprendere una sessione senza leggere il codice sorgente.

**File coinvolti.** `main.py`, eventuale `src/cli.py` e `src/observability.py`, `.env.example`, `README.md`, `src/agent/graph.py`, test della CLI.

1. Definisci input CLI minimi: ricerca configurata oppure repository/issue specifiche, limite dei risultati, ID della sessione e comando di ripresa. `argparse` della libreria standard è sufficiente per un MVP.
2. Fai restituire `SherpaAgent.run` lo stato o un risultato tipizzato. `main.py` deve stampare report, rifiuto o errore in modo chiaro. Evita `os._exit` per errori ordinari: interrompe cleanup, flush dei log e gestione delle risorse.
3. Sposta la generazione di `graph.png` fuori da `create_graph`: un normale avvio non deve richiedere un renderer remoto né scrivere immagini nel source tree. Offri eventualmente un comando esplicito di diagnostica.
4. Configura logging per fase, durata e categoria d'errore, senza token, body integrali delle issue, contenuti dei file o URL autenticati. `logging` e `LangSmith` sono strumenti disponibili; il tracing deve restare configurabile e rispettare i dati sensibili.
5. Scrivi `.env.example` senza valori segreti e un README con installazione, avvio, ripresa, limiti del clone, sandbox dei test, esempio breve di report e politica di nessuna PR automatica.

**Verifiche.** Configurazione mancante con errore comprensibile, CLI con fake graph, report stampato, ripresa con thread valido/non valido, nessun token nei log.

**Criterio di uscita.** Il progetto è eseguibile con un comando documentato e l'utente vede sempre un esito utile.

---

## Fase 11 — Integrazione, qualità e rilascio del primo MVP

**Obiettivo.** Dimostrare il flusso completo e i limiti di sicurezza prima di usare servizi esterni reali.

1. Prepara fixture Git locali e fake di GitHub, Gemini e sandbox. Prova almeno: issue accettata, issue rifiutata, nessun risultato, rate limit, clone fallito, inspect parziale, indagine inconcludente e report con pausa umana.
2. Testa gli invarianti di permesso: ciascun LLM vede soltanto i tool della propria fase; nessun tool di scrittura o pubblicazione è registrato. Verifica che contenuti malevoli in README o nell'issue non modifichino routing, path o comandi.
3. Esegui `python -m pytest -q`, `ruff check .` e `mypy src main.py`. Correggi prima errori che impediscono import e raccolta; poi comportamenti; infine stile e tipi. Se mypy non copre librerie senza stub, documenta eccezioni strette e motivate.
4. Esegui una sola prova reale controllata con credenziali e limiti bassi, dopo che i test offline sono verdi. Registra tempo, costo indicativo delle chiamate, numero di tool call e qualità del report. Non usare la prova reale come sostituto dei test.
5. Rivedi la documentazione rispetto al codice: nomi dei campi, modelli, percorsi, comandi e limiti devono coincidere. Archivia o correggi le guide di Ingestion se descrivono un approccio diverso da quello implementato.

### Definition of Done del progetto

- Il grafo percorre Discovery → Triage → Ingestion → Investigation → Advisory → Human Review, oppure termina con un errore/rifiuto esplicito.
- Checkout, branch e SHA analizzati sono verificabili; letture e output rispettano budget e confini del workspace.
- I test della repository target sono eseguiti soltanto in sandbox, oppure il report dichiara che non sono stati eseguiti.
- Il report distingue evidenze e inferenze e arriva a una pausa umana riprendibile.
- Nessuna fase modifica o pubblica automaticamente nella repository target.
- Test offline, lint e controllo statico dei tipi passano, salvo eccezioni documentate e circoscritte.

## Come usare questa roadmap durante lo sviluppo

Affronta **una fase alla volta**. All'inizio scrivi in poche righe input, output e condizioni di fallimento; poi implementa le funzioni pure; solo dopo collega tool e nodo. Quando chiedi una review, presenta la fase, i file toccati, i test eseguiti e l'incertezza rimasta: così il feedback può guidare il tuo codice senza sostituirsi alla tua implementazione.
