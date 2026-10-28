# Sentinella — automazioni per Codex e Claude su Windows

**Programma messaggi, gestisci le richieste delle chat e segui il lavoro dei tuoi assistenti AI anche quando non sei al PC.**

Sentinella è un'app gratuita e open source per Windows, con interfaccia in italiano. Lavora con le applicazioni desktop **OpenAI Codex** e **Claude**: invia messaggi programmati alle chat selezionate, rileva domande e gestisce i consensi file nelle cartelle che hai autorizzato.

[Scarica Sentinella per Windows](https://github.com/GMK-AGENCY/Sentinella/releases/latest) · [Installazione](#installazione) · [Segnala un problema](https://github.com/GMK-AGENCY/Sentinella/issues/new/choose)

**Windows 11 a 64 bit · Italiano · Licenza MIT · Installer senza Python da installare**

## Cosa puoi fare

| Funzione | A cosa serve |
| --- | --- |
| Messaggi programmati per Codex e Claude | Scegli le chat, scrivi il messaggio e imposta un invio singolo o periodico. |
| Invii durante il lavoro | Consenti, per ogni programmazione, l'invio mentre la chat lavora o mentre usi il PC. |
| Risposte durante l'assenza | Conserva le domande per il tuo ritorno e invita la chat a continuare le attività indipendenti già autorizzate. |
| Consensi nelle cartelle autorizzate | Gestisci richieste riconoscibili di lettura e scrittura solo nei percorsi scelti. |
| Raccolta di messaggi | Organizza testi riutilizzabili senza riscriverli a ogni programmazione. |
| Agenda e registro invii | Vedi cosa è previsto, perché un invio è in attesa e quali consegne hanno una ricevuta. |

Le bozze nelle chat restano protette. Un invio senza ricevuta non viene ripetuto automaticamente.

Il timer gira nel programma: **non richiede una chat di controllo o automazioni di un agente**. Per Codex, l'invio e la lettura della ricevuta sono gestiti dal processo locale di Sentinella.

## Installazione

Requisiti: Windows 11 a 64 bit e le app desktop che si desidera controllare. **La versione Windows installabile include Python e tutte le dipendenze: non devi installare Python.** Non serve una chiave API OpenAI.

1. Scarica `Sentinella-0.2.9-Windows-x64-Setup.exe` dalla release.
2. Esegui l'installer per il tuo utente: non richiede diritti di amministratore.
3. Avvia **Sentinella** dal menu Start. Il primo avvio è in pausa.
4. In **Impostazioni**, premi **Rileva applicazioni**, scegli le funzioni e le cartelle già autorizzate, poi togli la pausa quando sei pronto.

L'app si installa in `%LOCALAPPDATA%\Programs\Sentinella`. Preferenze, messaggi, database e ricevute restano separati in `%LOCALAPPDATA%\Sentinella`; aggiornamento e disinstallazione conservano questi dati. La disinstallazione rimuove soltanto il collegamento di avvio automatico appartenente a questa installazione. L'avvio con Windows è facoltativo e disattivato all'installazione.

L'eseguibile non è firmato digitalmente. L'installer non modifica le protezioni Windows né tenta di superarne i controlli.

### Avvio dai sorgenti

Per sviluppare o usare i sorgenti serve Python 3.12 con Tkinter. Questa modalità conserva i dati nella cartella del progetto e non importa automaticamente quelli dell'app installata.

1. Scarica ed estrai il progetto in una cartella scrivibile dall'utente.
2. Esegui `Installa Sentinella.cmd`. Installa solo le dipendenze Python nella cartella `.venv` del progetto.
3. Esegui `Avvia Sentinella.cmd`.
4. Apri le app da controllare e, in **Impostazioni**, premi **Rileva applicazioni**. I controlli comuni valgono per entrambe le app rilevate.
5. Configura soltanto le cartelle già autorizzate. Attiva le funzioni desiderate e togli la pausa globale quando sei pronto.

Al primo avvio Sentinella è **in pausa**, con risposte automatiche, consensi, avvio con Windows e messaggi disabilitati. Una configurazione esistente non viene sostituita. Il lettore delle notifiche non apre automaticamente richieste di accesso Windows: un accesso assente o negato viene mostrato come blocco.

## Messaggi programmati

Apri **Programmazioni** e seleziona una riga: testo e opzioni si modificano direttamente sotto l’elenco. I comandi di salvataggio e riuso sono riuniti in basso. Solo **Nuova programmazione** usa la scelta dei destinatari seguita da Scrivi e programma. Il primo invio parte dopo l'intervallo scelto, senza recuperare gli arretrati con una raffica.

La **Panoramica** mostra ogni messaggio con frequenza, prossima attività e ultima ricevuta disponibile. Il pulsante pausa/ripresa della riga agisce soltanto su quel messaggio; un clic sul nome apre l'editor. I dettagli degli invii si consultano nel Registro invii. Le programmazioni sospese e le bozze mostrano il motivo dell'attesa al posto di un conto alla rovescia. **Diagnostica** apre gli aggiornamenti dei componenti separatamente: un processo aggiornato non dimostra che un messaggio sia stato ricevuto.

**Messaggi salvati** conserva testi riutilizzabili. Usare un testo compila l'editor, senza creare o inviare una programmazione.

- La programmazione conserva ID, versione, testo, scadenza e ultimo invio in modo persistente.
- Codex viene identificato attraverso l'ID della chat e il titolo verificato, non scegliendo una chat dal nome simile.
- I titoli duplicati bloccano l'invio nativo. La chat deve essere disponibile nella barra laterale.
- Il controllo **Invia anche mentre lavora** permette la consegna alla chat attiva tramite il suo pulsante nativo, su entrambe le applicazioni. Le altre programmazioni aspettano che la chat sia libera.
- **Invia anche mentre uso il PC** consente gli invii nativi durante l’uso del PC, selezionando la chat esatta quando l’app destinataria è in background, senza prendere il focus. Se stai usando un’altra chat nella stessa app, aspetta per non interromperti. Durante la digitazione nel campo messaggio aspetta una breve pausa. Se spento, vale l'inattività impostata. È spento per nuove programmazioni; i messaggi già configurati conservano il comportamento precedente. Cambiare soltanto queste opzioni mantiene la scadenza esistente.
- Una bozza dell'utente, un dialogo aperto, una pausa o un'identità non verificabile impediscono l'invio.
- La prenotazione viene salvata prima dell'invio. Un invio dall'esito incerto non viene ripetuto automaticamente.
- Codex è indicato come consegnato soltanto dopo la lettura di un vero messaggio utente nello storico della chat corretta. Il confronto ammette esclusivamente il ritorno a capo terminale aggiunto dal compositore nativo, registrandolo nella ricevuta.
- Se il timer viene modificato durante una consegna, la ricevuta della vecchia versione non azzera il nuovo timer.

Le letture non cambiano focus. Quando l’invio durante l’uso del PC è attivo, Sentinella può selezionare la chat esatta nell’applicazione in background tramite i controlli nativi, senza simulare mouse o tastiera. Ricontrolla il primo piano prima della selezione e dell’invio. Se l’app destinataria è in uso su un’altra chat, conserva il lavoro e attende. Le ricevute Claude provengono soltanto da messaggi utente della sessione identificata; la rimozione dello spazio terminale da parte dell'app viene dichiarata nella ricevuta. Un residuo esatto della bozza di Sentinella viene salvato integralmente in `logs/preserved-drafts` prima della pulizia; bozze diverse restano nel campo.

## Modifiche e pulizia dei registri

Prima di lasciare un editor con modifiche non salvate, **Salva modifiche** le registra e **Scarta modifiche** ricarica i valori salvati. Chiudere l'avviso o premere Esc annulla la navigazione. Gli interruttori delle impostazioni indicati come immediati vengono salvati separatamente dai tempi ancora in modifica.

**Elimina tutto**, a destra dei filtri di Registro invii e Notifiche, richiede conferma e riguarda l'intero registro, anche con filtri attivi. Rimuove i contenuti storici e gli export di ricevuta già conclusi associati. Non cancella chat, notifiche Windows, cartelle autorizzate o configurazioni. Scadenze, bozze e registrazioni operative necessarie a evitare duplicati restano conservate. Le notifiche eliminate non ricompaiono dopo un riavvio; i nuovi eventi continuano a essere raccolti. Nella modalità sorgenti i dati sono in `logs/`; nell'app installata in `%LOCALAPPDATA%\Sentinella\logs`.

## Messaggi salvati

La sezione **Messaggi salvati** organizza testi ricercabili e modificabili in raccolte. Un archivio nuovo include quattro raccolte con dodici messaggi standard; gli archivi esistenti conservano i testi personali. I soli messaggi predefiniti riconosciuti integralmente e mai modificati vengono aggiornati senza riscrivere l'archivio alla lettura. **Nuova raccolta** crea una categoria; **Nuovo messaggio** prepara un testo nella raccolta scelta. **Salva nei messaggi** apre una finestra per scegliere nome e raccolta senza salvare o attivare la programmazione; **Usa messaggio salvato**, nell’editor delle programmazioni, inserisce un testo della raccolta nel messaggio. Il testo già presente nell'editor è protetto da conferma. Eliminare una raccolta richiede conferma con il numero dei messaggi che verranno rimossi; le programmazioni già create conservano il proprio testo. L'archivio locale `message-library.json` e la sua copia `.bak` contengono dati privati e sono esclusi dai pacchetti pubblici.

Nella scelta delle chat, **ID sessione** mostra un identificativo abbreviato quando è ricavabile senza ambiguità. Il filtro Cerca include anche l'ID. Un nome duplicato non viene associato arbitrariamente a una sessione: rinomina la chat nell'applicazione e aggiorna l'elenco.

## Sorgenti e GitHub

Il progetto è distribuito con [licenza MIT](LICENSE). Per contribuire consulta [CONTRIBUTING.md](CONTRIBUTING.md); per le segnalazioni di sicurezza consulta [SECURITY.md](SECURITY.md).

`python tools/prepare_github.py --output dist/github-ready/Sentinella --initialize-git` prepara un nuovo repository locale sul ramo `main`, copiando soltanto i file pubblici autorizzati. Non copia `.git`, configurazioni, messaggi personali, registri o ricevute dell'installazione sorgente; non crea repository remoti e non esegue push. La destinazione deve essere nuova. Il commit iniziale usa un'identità generica del progetto; imposta la tua identità pubblica prima dei contributi successivi.

Il workflow Windows esegue controlli statici, test, pacchetto sorgente, bundle e installer, conservando i pacchetti con i relativi SHA-256 come artifact. Per pubblicare una release occorre prima verificare il risultato della CI nel repository remoto.

## Domande e consensi

In **Impostazioni → Frequenza dei controlli**, **Attiva dopo** indica quanti minuti senza input di mouse e tastiera attendere prima di rispondere, gestire consensi o cambiare chat. Ogni nuovo input fa ripartire l'attesa. Puoi scrivere un valore personalizzato (per esempio `0,5` per 30 secondi); `0` disattiva l'attesa. Il valore iniziale resta 30 secondi. Questa soglia non toglie la pausa generale e non modifica gli intervalli dei messaggi programmati.

Le risposte di assenza invitano a conservare la domanda e a continuare solo il lavoro già autorizzato. Non concedono nuove autorizzazioni.

I consensi automatici sono facoltativi e disabilitati all'installazione. Riguardano soltanto letture e scritture file riconoscibili nelle cartelle delegate. Restano esclusi eliminazioni, shell, rete, credenziali e richieste non verificabili. Il riconoscimento dipende dall'interfaccia delle applicazioni: **non è un confine di sicurezza**. Abilitalo solo nelle sessioni e cartelle di cui accetti esplicitamente il comportamento.

Le schede inline «Autorizzazioni» di Codex sono riconosciute anche senza un ruolo di dialogo. Il percorso completo viene letto dalla descrizione nativa della risorsa, eventualmente dopo averne mostrato il suggerimento durante l'inattività. Un percorso scritto nella spiegazione del modello non dimostra la cartella effettivamente richiesta. Se il percorso nativo non è verificabile, il consenso resta sospeso. Richieste nuove per lo stesso file vengono distinte tramite l'identità nativa della scheda.

Quando il nome della risorsa va a capo, il centro del suo rettangolo può cadere nello spazio vuoto tra le righe. Sentinella verifica fino a nove punti interni e mostra il suggerimento soltanto su un punto appartenente alla risorsa effettiva; un elemento coperto non viene premuto.

### Scrittura Codex su Windows

Il percorso desktop **consenso → creazione → rilettura**, e la successiva modifica del file, sono stati verificati con il writer ordinario di Codex, senza elevazione e con soglia di inattività di 10 secondi. Per creare un file esterno al workspace, nel collaudo Windows il grant sul solo file inesistente non era sufficiente: Codex ha dovuto richiedere la **cartella padre**, già compresa nelle cartelle delegate. Sentinella non amplia automaticamente una richiesta su un file alla cartella padre e non approva cartelle fuori delega.

Se Codex segnala «Failed to write file» usando `apply_patch` dopo un consenso, può usare il normale writer tramite comando, mantenendo il medesimo ambito già autorizzato. Nella versione desktop collaudata questo percorso ha creato e aggiornato i file, mentre il tool `apply_patch` continuava a fallire. Il consenso di Sentinella autorizza soltanto l'operazione file riconoscibile: non approva nuovi comandi shell o esecuzioni fuori sandbox.

## Avvio e recupero

In Impostazioni, **Recupera scritture bloccate** abilita lo stesso comportamento per entrambe le applicazioni. Sentinella legge in sola lettura gli errori effettivi dei tool file nella cronologia locale. Un errore recente di `apply_patch`, `Write` o `Edit`, su file compresi nelle cartelle delegate, può generare una sola richiesta di ripresa con un writer ordinario. Il messaggio conserva l'operazione originale e richiede la rilettura del file; non contiene codice estratto dalla chat. Un diniego di permesso non viene aggirato.

Il recupero appartiene al worker del programma e non richiede chat di controllo o sveglie. Rispetta pausa, inattività, ambito dei consensi e bozze. La prenotazione viene registrata prima dell'invio nativo; soltanto un messaggio utente autentico con testo esatto conferma la consegna. Gli esiti incerti restano sospesi senza reinvio. Stato e ricevute sono in `logs/file-recovery-status.json`, `logs/file-recovery.sqlite3` e `logs/file-recovery-receipt-*.json`. La ricevuta conferma la richiesta di recupero, non la riuscita della scrittura: quest'ultima richiede la verifica del file effettivo.

Gli interruttori Domande e Consensi file valgono per tutte le applicazioni rilevate. **Invia anche mentre lavora** vale per ogni destinatario: quando l'interfaccia lo permette, il messaggio viene ricevuto o accodato durante l'elaborazione, senza premere Interrompi e senza sovrascrivere bozze. Le app non ancora aperte vengono attese.

- Una sola istanza del launcher e della GUI.
- La X chiude Sentinella e i suoi worker; la chiusura intenzionale non provoca una riapertura.
- Avvio con Windows facoltativo, per il solo utente, dopo l'accesso.
- Il launcher recupera la GUI dopo crash o heartbeat scaduto.
- Gli adattatori sono processi isolati: limite di 12 secondi, terminazione dei figli e traccia del punto di blocco.
- Pausa, configurazioni e timer persistono ai riavvii.

Le app devono essere aperte e la sessione Windows sbloccata. Le finestre minimizzate, le chat non esposte dalla barra laterale e i cambiamenti delle interfacce possono impedire la consegna: Sentinella deve mostrare il motivo e non dichiarare un falso successo.

## Dati locali e privacy

`approval-config.json` contiene preferenze, percorsi e deleghe. `messages-config.json` contiene destinatari e messaggi. `logs/` contiene database, registri e ricevute che possono includere testi delle chat. Questi dati, l'ambiente Python e le prove personali sono esclusi da Git e dall'archivio pubblico.

Prima di allegare un log a una issue, elimina nomi, percorsi, testo delle chat e qualsiasi dato riservato. Vedi [SECURITY.md](SECURITY.md).

## Sviluppo e verifica

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe tests/verify_native_controls.py
.\.venv\Scripts\python.exe tools/build_release.py
```

La verifica nativa opzionale apre due finestre **PROVA LOCALE** per controllare il riconoscimento di domanda e consenso. Non invia messaggi e non approva richieste reali. I test automatici usano cartelle e database temporanei. Le verifiche su chat reali richiedono invece un destinatario e un messaggio esplicitamente autorizzati: non sono eseguite in CI.

Per collaudare un consenso Codex reale senza usare mouse o tastiera, è disponibile una prova esplicita con il protocollo ufficiale App Server. Richiede Codex CLI già installato e autenticato; usa un turno del modello configurato. Crea una sessione effimera in sola lettura, autorizza una sola creazione con la politica file di Sentinella e verifica il contenuto del file effettivamente scritto. Non avvia i worker desktop, non cambia le impostazioni e non usa le chat o i timer esistenti. Scegli una cartella di prova e una cartella di report nuova:

```powershell
Sentinella.exe --verify-codex-permissions --workspace C:\Prove --output C:\Report\consenso-nuovo
```

Dai sorgenti usa `python codex_permission_selftest.py` con gli stessi argomenti. Il limite è 50 secondi; una richiesta diversa viene interrotta. Il risultato autentico è in `report.json`, con richiesta, decisione, esito file e hash. Il test verifica la politica e la scrittura attraverso App Server; il riconoscimento dei controlli desktop va verificato separatamente.

Il workflow GitHub esegue i test su Windows con Python 3.12. L'archivio pubblico è costruito tramite una lista esplicita di file e verificato contro inclusioni di dati locali.

### Build Windows

Installa `requirements-build.txt` nell'ambiente di sviluppo, poi esegui `python tools/build_windows.py --iscc PERCORSO_ISCC`. Il comando crea il programma autonomo in una cartella di build nuova, indicata in `build/latest-windows.json`, e l'installer in `dist/`. Ogni build è separata dalla precedente. Usa una versione di Inno Setup scaricata dal sito ufficiale e verificane la firma. Non sono inclusi configurazioni, messaggi o log della macchina di sviluppo. Le licenze delle dipendenze incluse sono nella cartella `THIRD_PARTY_NOTICES` del programma.

Per verificare il binario con un profilo di prova separato, imposta `SENTINELLA_DATA_DIR` a una cartella assoluta vuota ed esegui `Sentinella.exe --smoke-test`. La prova richiede un profilo pulito in pausa, controlla GUI e worker e li chiude. Non usa le chat e non concede permessi. `--diagnose` verifica il caricamento delle dipendenze senza aprire la GUI.

## Limiti della verifica

Il supporto verificato riguarda Windows 11 e le versioni delle app usate nel collaudo. Non è garantita la compatibilità con tutte le versioni future delle loro interfacce. La consegna Codex è verificata con ricevuta; per i trasporti desktop legacy, un evento di clic va distinto dalla ricezione effettiva. Non sono implementate approvazioni di autenticazione, sicurezza di Windows o operazioni distruttive.

Nel collaudo di Codex desktop 26.1021.3705.0, `apply_patch` del relativo host ha restituito «Failed to write file», anche nell'aggiornamento di un file esistente dopo il grant della cartella. La creazione e l'aggiornamento tramite il writer ordinario sono stati verificati nella stessa chat dopo consensi nativi gestiti da Sentinella. Il difetto di `apply_patch` resta un limite di compatibilità documentato; non viene dichiarato riparato da Sentinella. L'app non cambia le impostazioni di sicurezza e non considera un clic sul consenso una prova di scrittura riuscita.
