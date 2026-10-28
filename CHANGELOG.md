# Modifiche

## 0.2.3

- Diagnostica con stato in corso e risultato vicino ai comandi; il riavvio del lettore viene confermato solo dopo una nuova lettura delle notifiche.
- Selezione chat con ID sessione disponibile, aggiornamento accanto alla selezione e istruzioni raccolte nel footer. Un’identità ambigua resta bloccata.
- Quattro raccolte iniziali con dodici modelli modificabili per archivi nuovi; raccolte e testi esistenti preservati. Creazione, rinomina, ricerca e riuso delle raccolte nell’editor.
- Editor e biblioteca riallineati, riepilogo destinatari accanto alla previsione di invio, tooltip sugli interruttori e scrollbar visibili solo quando servono.
- Icona dedicata, metadati versione dell’eseguibile, licenza MIT e preparazione di un checkout pubblico senza dati o cronologia locali.

## 0.2.2

- Risolto l'elenco chat vuoto: lettura delle date corretta e completamento garantito di caricamento, errori e nuovi tentativi.
- Un solo stato operativo condiviso da Panoramica, programmazioni e dettagli. I comandi espliciti Pausa/Riprendi sono distinti dallo stato e non cancellano ricevute pendenti.
- Agenda per giorno con attese e pause separate; pausa di tutte le programmazioni esposta direttamente nella barra delle azioni.
- Editor con salvataggio in alto e scelta diretta di un modello con ricerca e anteprima. La raccolta mostra elenco e modifica affiancati, con validazione vicino ai campi.
- Bozze dell'editor protette durante nuova programmazione o cambio destinatario; una modifica a un messaggio sospeso conserva la pausa.
- Ricevute Claude durante il lavoro verificate tramite testo esatto, identità della sessione e corrispondenza fra accodamento, messaggio umano e assorbimento nella sessione. Nessun reinvio del tentativo riconciliato.
- Collaudi completi dei flussi Tk in profili isolati e controllo statico sui nomi indefiniti prima della build.

## 0.2.1

- Panoramica con righe leggibili, scorrimento solo verticale, azioni pausa/ripresa e modifica direttamente sul messaggio. Motivo dell'attesa e ricevuta sono associati alla singola programmazione; i dettagli tecnici si aprono da Diagnostica.
- Nessun conto alla rovescia per bozze, pause o ricevute in verifica. I filtri delle chat sono sempre visibili; i tooltip chiariscono l'effetto degli interruttori anche da tastiera.
- Sezione Messaggi salvati con testi riutilizzabili, ricerca, modifica e copia di recupero locale. Il riuso compila l'editor e non invia nulla; il testo già presente è protetto da conferma.
- Opzione per programmazione «Invia anche mentre uso il PC», distinta dall'attività della chat. Le nuove programmazioni la propongono spenta; quelle esistenti conservano il comportamento precedente. Cambiare soltanto le opzioni non azzera scadenza o versione.
- Cartelle autorizzate più leggibili, elenco programmazioni compatto e dettaglio ampio, comandi diagnostica allineati e pulsante globale unico con icona play/pausa.
- Stesso comportamento per gli invii nativi in background su entrambe le applicazioni: solo nella chat esatta già aperta, con primo piano ricontrollato prima di ogni modifica e senza navigare durante l'uso del PC.
- Invii periodici Claude con prenotazione persistente della bozza e conferma da messaggi utente autentici della cronologia locale; il ritorno del pulsante Invia non equivale più a consegna.
- Recupero del testo residuo dopo una consegna verificata: copia integrale su disco prima della pulizia, solo per la bozza esatta della programmazione e nella stessa sessione. Bozze modificate o estranee restano nel campo.
- Ricevute controllate anche durante l'uso del PC, senza azioni sull'interfaccia. Nessun reinvio automatico di prenotazioni prive di ricevuta.
- Riconciliazione delle precedenti consegne documentate senza modificare testo, versioni o scadenze. La sola rimozione dello spazio finale da parte di Claude è registrata esplicitamente nella ricevuta.

## 0.2.0

- Interfaccia riprogettata come workspace con navigazione laterale: Panoramica, Programmazioni, Registro invii, Notifiche e Impostazioni.
- Panoramica con indicatori distinti, prossima scadenza, stato corrente e ultimo risultato; contatori coerenti anche durante la pausa generale.
- Programmazioni ed eventi con elenco e dettaglio completo, selezione stabile e barre di scorrimento; editor in due passaggi con tutte le opzioni precedenti.
- Registro integrato nella navigazione, con filtri per identità della programmazione, periodo ed esito applicati prima del limite dei risultati.
- Notifiche con ricerca, filtri e contenuto sempre accessibile; impostazioni raggruppate verticalmente per comportamento, tempi, deleghe e diagnostica.
- Layout verificato su profilo locale di prova a 1280×850 e 1040×790; configurazioni, timer e bozze delle applicazioni esterne restano separati dalla presentazione.

## 0.1.5

- Stato di attivazione separato dall'attività: la pausa prevale anche su bozze e invii da verificare.
- Conto alla rovescia in minuti e secondi, scadenza visibile e ritardo esplicito. I rinvii tecnici non spostano la scadenza pianificata.
- Registro invii con destinatario, orario, esito e motivo; le ricevute confermate si distinguono dagli invii senza verifica.
- Ripresa di un messaggio senza azzerare timer, versione o prenotazioni incerte.
- Panoramica con prossima scadenza, ultima attività e versione dell'app sempre visibile.

## 0.1.4

- «Invia anche mentre lavora» viene salvato per tutti i destinatari, con le stesse protezioni delle bozze.
- Controlli comuni per domande, consensi e recupero delle scritture, senza interruttori distinti per applicazione.
- Recupero facoltativo degli errori reali dei tool file: una sola richiesta di writer alternativo entro le cartelle delegate, con prenotazione persistente e ricevuta autentica. Nessun reinvio quando l'esito è incerto.
- Esclusi errori storici, richieste distruttive, permessi negati e nuovi tentativi originati dal recupero stesso. Pausa e soglia di inattività restano prioritarie.

## 0.1.3

- Corretto il puntamento delle risorse Codex con nome su più righe: il centro del rettangolo poteva cadere nel vuoto e impedire la lettura del percorso completo.
- Verifica limitata a nove punti con controllo dell'identità della risorsa; restano attive le guardie di pausa, inattività e cartelle delegate.
- I consensi rilevati durante l'uso del PC restano in coda per l'inattività, senza attendere un altro intervallo di controllo. Un breve controllo dopo l'approvazione segue eventuali richieste concatenate, poi ripristina la frequenza normale.
- Collaudi desktop reali di consenso sulla cartella, creazione, rilettura e aggiornamento con il writer ordinario, senza elevazione. Documentata la differenza fra grant del file e grant della cartella durante la creazione.
- Documentato il percorso compatibile con il writer ordinario per la versione Codex in cui `apply_patch` continua a fallire.

## 0.1.2

- Lettura del percorso Codex dalla descrizione nativa della risorsa, con suggerimento mostrato solo sotto le guardie di pausa e inattività.
- Distinzione delle richieste consecutive per lo stesso file tramite identità della scheda; la spiegazione del modello non autorizza percorsi aggiuntivi.
- Collaudo esplicito senza interfaccia tramite Codex App Server: richiesta reale, politica file condivisa, creazione effettiva e verifica del contenuto; sessione effimera e limite di 50 secondi.
- Documentato separatamente il difetto del writer apply_patch osservato nell'host desktop dopo un consenso concesso.

## 0.1.1

- Soglia di inattività configurabile in minuti, con spiegazione del rilevamento di mouse e tastiera e conservazione di pausa e timer.
- Riconoscimento delle schede inline di autorizzazione Codex; rifiuto automatico di consensi il cui percorso è disponibile solo nella spiegazione del modello.

## Prima distribuzione

- App Windows autonoma e installer per utente, con Python incluso e dati separati dai file del programma.
- Verifica di installazione, aggiornamento e disinstallazione con conservazione dei dati personali.
- Ripresa del ciclo GUI dopo un errore di I/O; il watchdog distingue una finestra viva da un ciclo operativo fermo.
- Riconoscimento delle domande finali di Claude anche senza badge “richiede input”, escludendo risposte già date e generazione in corso.
- Ricerca delle chat e arresto del lettore con terminazione dei processi figli e scadenze effettive.
- Mittente locale Codex indipendente da chat di controllo e automazioni di agenti.
- Invio autorizzato alle chat attive, con prenotazione e ricevuta dello storico reale.
- Conservazione dell'identità del destinatario e della politica di invio durante le modifiche dalla GUI.
- Riconoscimento dell'app dopo aggiornamenti Store senza accettare un altro pacchetto.
- Timeout effettivi degli adattatori, terminazione dei figli e diagnostica degli stalli.
- Avvio sicuro da una cartella nuova, con funzioni disabilitate e pausa attiva.
- Test di regressione, verifica nativa opzionale e distribuzione che esclude i dati personali.
