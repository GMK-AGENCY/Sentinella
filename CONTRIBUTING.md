# Contribuire a Sentinella

Sentinella è un'applicazione locale per Windows. I contributi sono distribuiti con la [licenza MIT](LICENSE); le dipendenze conservano le proprie licenze.

## Prima di iniziare

Per un difetto, apri una segnalazione con la versione di Sentinella, di Windows e dell'app desktop coinvolta, i passaggi per riprodurlo e il risultato atteso. Preferisci esempi sintetici: non pubblicare conversazioni, destinatari, cartelle delegate o registri personali. Per vulnerabilità e possibili invii o consensi errati, segui [SECURITY.md](SECURITY.md).

Per cambiamenti estesi, descrivi prima il problema e il percorso proposto. Mantieni ogni pull request concentrata su un problema verificabile. Una modifica a un'interfaccia desktop può richiedere aggiornamenti agli adattatori: specifica la versione dell'app osservata e distingui una simulazione da un collaudo reale.

## Ambiente di sviluppo

Usa Windows 11 a 64 bit e Python 3.12 con Tkinter, in un clone dedicato allo sviluppo. Non riutilizzare la cartella di un'installazione personale con messaggi e timer attivi.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m pip check
```

Le dipendenze di sviluppo includono quelle dell'app. Non servono chiavi API per i test automatici. L'accesso alle app reali non deve essere un requisito della suite.

## Verifiche prima della pull request

```powershell
.\.venv\Scripts\python.exe tools/check_python.py
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

Esegui prima i test pertinenti alla modifica, poi la suite completa prima della consegna. Riporta i comandi eseguiti e i limiti della verifica. Non presentare una finestra aperta, un clic o un worker vivo come prova di consegna o di scrittura riuscita.

I test usano cartelle e database temporanei. Le prove Tk possono creare finestre di collaudo locali: non devono controllare le chat personali. Non avviare launcher, worker desktop o test su chat reali come effetto collaterale della suite. I collaudi reali richiedono destinatario, messaggio e operazione autorizzati esplicitamente e vanno tenuti separati dalla CI.

## Regole da preservare

- Pausa generale, pause delle singole programmazioni e chiusure volontarie restano autorevoli.
- Una bozza non salvata non va sostituita; un invio incerto non va ripetuto automaticamente.
- Titolo e identità del destinatario devono essere verificati. Non scegliere chat dal nome simile.
- Una consegna richiede una ricevuta autentica. La richiesta di recupero di una scrittura non dimostra che il file sia stato scritto.
- Domande, consensi, invii e recuperi restano entro le funzioni e cartelle scelte dall'utente; un diniego non va aggirato.
- Chiamate e attese verso processi e interfacce devono avere limiti espliciti. Nei collaudi autonomi ogni singola operazione deve restituire controllo entro 60 secondi.

## Dati e file pubblicabili

La lista esplicita in `tools/build_release.py` definisce il contenuto dell'archivio sorgenti. Quando aggiungi un modulo, una risorsa o un documento necessario, aggiorna la lista e i test di rilascio nella stessa pull request. `.gitignore` da solo non è una verifica di privacy: non rimuove file già tracciati né dati presenti nella cronologia Git.

Non aggiungere configurazioni personali, database, ricevute, bozze conservate, archivi della biblioteca, screenshot non anonimizzati o output di build. Usa nomi, percorsi e identificativi sintetici nei test. Non copiare lo storico di un ambiente personale nel repository pubblico.

Per verificare l'archivio sorgenti, scegli un nome di output nuovo: il builder non sovrascrive un archivio esistente.

```powershell
.\.venv\Scripts\python.exe tools/build_release.py --output dist/Sentinella-source-review.zip
```

Le istruzioni di build del programma e dell'installer sono nel [README](README.md#build-windows). La disponibilità di un archivio sorgenti non dimostra che l'installer sia stato compilato o collaudato.

## Descrizione della pull request

Spiega problema, comportamento risultante e verifiche. Per cambiamenti visivi allega schermate di un profilo con dati simulati e indica dimensione della finestra e versione dell'app. Se una prova manca, dichiaralo; non includere log completi per compensare una verifica non eseguita.
