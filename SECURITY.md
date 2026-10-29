# Sicurezza e segnalazioni

## Ambito e limiti

Sentinella opera con i permessi dell'utente Windows corrente. Il riconoscimento dei controlli desktop aiuta a rispettare la configurazione scelta, ma non è un confine di sicurezza e non sostituisce le autorizzazioni di Windows o dell'app destinataria. I cambiamenti delle interfacce possono richiedere un aggiornamento degli adattatori.

Le risposte automatiche e i consensi file sono facoltativi e disabilitati al primo avvio. Abilitali soltanto per sessioni e cartelle di cui accetti il comportamento. Un consenso file riconosciuto non autorizza eliminazioni, comandi shell, rete, credenziali o operazioni fuori dalle cartelle delegate. Un percorso citato dal modello non dimostra da solo l'ambito della richiesta nativa.

L'opzione separata **Approva automaticamente i comandi** è disattivata per default. Se la abiliti, Sentinella può premere **Consenti una volta** sulle richieste native di esecuzione riconosciute, comprese quelle che segnalano comandi pericolosi. Questo può consentire cancellazioni, accesso alla rete e operazioni irreversibili anche fuori dalle cartelle autorizzate: il filtro dei consensi file non limita questa modalità. Abilitala soltanto se accetti le conseguenze dei comandi proposti nelle sessioni controllate. Non concede approvazioni permanenti e non gestisce consensi di Windows, accessi o impostazioni di sicurezza. Restano obbligatori pausa generale, inattività configurata, identità del processo e riconoscimento del documento nativo; una pagina web integrata non basta. Disattivare l'opzione revoca anche un clic preparato ma non ancora eseguito.

Non è dichiarata una finestra di supporto a lungo termine per le versioni precedenti. Nelle segnalazioni indica la versione esatta; non presumere che una vecchia versione sia protetta perché il processo è ancora in esecuzione. Non sono promessi tempi di risposta o di correzione.

## Se osservi un invio o un consenso errato

1. Metti in pausa Sentinella e disabilita la funzione interessata. Se necessario, chiudi l'app.
2. Non forzare nuovamente un invio incerto: verifica prima il messaggio effettivamente presente nella chat destinataria.
3. Conserva in locale l'evidenza originale e annota orario, versione e comportamento osservato. Non allegare l'intero profilo dell'app.
4. Segnala il problema tramite il canale descritto sotto, rimuovendo i dati personali dalla copia destinata alla condivisione.

Un evento di invio, un clic sul consenso o una richiesta di recupero non dimostrano rispettivamente ricezione, autorizzazione corretta o scrittura riuscita.

## Segnalazione di vulnerabilità

Se nella scheda **Security** del repository GitHub è disponibile **Report a vulnerability**, usa quella segnalazione privata. La disponibilità del canale dipende dalle impostazioni del repository; questo documento non afferma che sia già attivo.

Se il canale privato non è disponibile, apri soltanto una issue per chiedere un contatto privato, senza dettagli sfruttabili, allegati sensibili o istruzioni di riproduzione di una vulnerabilità. Non pubblicare una prova d'attacco in una issue ordinaria. Non inventare indirizzi email o inviare dati ad account non indicati dal progetto.

In privato, descrivi versione di Sentinella e delle app coinvolte, prerequisiti, passaggi minimi, impatto atteso e risultato osservato. Preferisci una riproduzione con file e chat di prova. Non eseguire operazioni su dati o sessioni altrui per dimostrare il problema.

## Dati da non pubblicare

- `approval-config.json`, `messages-config.json`, `message-library.json` e tutte le copie di backup;
- database, ricevute, registri e bozze conservate in `logs/` o in un altro profilo dati;
- testi delle chat, destinatari, identificativi di sessione o messaggio e cartelle delegate;
- percorsi del profilo Windows, nomi personali e schermate non anonimizzate;
- token, cookie, password, chiavi o altre credenziali.

I dati dell'app installata possono trovarsi in `%LOCALAPPDATA%\Sentinella`, quelli dei sorgenti nella cartella di lavoro; `SENTINELLA_DATA_DIR` può scegliere un profilo diverso. Un file escluso da Git può comunque contenere dati riservati. Anche una ricevuta tecnicamente valida può includere il messaggio completo.

Per una segnalazione ordinaria, condividi solo le righe strettamente necessarie, dopo averne verificato manualmente il contenuto. La scansione automatica del pacchetto non garantisce l'anonimizzazione di log e screenshot.

## Distribuzione

Il codice del progetto è distribuito con [licenza MIT](LICENSE). Le licenze dei componenti inclusi nel programma sono separate e vanno mantenute nel bundle. Non descrivere un eseguibile come firmato digitalmente se la release non ne documenta la firma. Non disattivare le protezioni del sistema per installare o collaudare l'app.
