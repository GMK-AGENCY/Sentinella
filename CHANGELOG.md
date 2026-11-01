# Modifiche

## 0.2.23 — in sviluppo

- Riconciliazione Codex della singola nuova riga finale aggiunta al testo nella versione qualificata: hash osservato e atteso restano distinti e la ricevuta originale viene conservata. Ogni altra variazione resta bloccante; nessun reinvio.
- Il journal ricontrolla questa equivalenza dal messaggio immutabile e dall'item nativo prima di aggiornare il timer. Le ricevute precedenti alla consegna vengono rifiutate.
- Questa correzione non abilita né qualifica lo steering autonomo delle sessioni desktop durante turni lunghi.

## 0.2.22

- Invii periodici gestiti dal programma tramite i collegamenti nativi locali di Codex e Claude, identificando la sessione per ID. Nessuna selezione di finestre, digitazione nel compositore o dipendenza da chat e sveglie di controllo.
- Registro persistente con prenotazione prima dell'effetto, un solo tentativo aperto per sessione e avanzamento del timer soltanto dopo una ricevuta autentica. Arresti del mittente e risposte perse avviano la riconciliazione del medesimo tentativo, senza reinvio automatico.
- Rispettate separatamente le opzioni di invio mentre la chat lavora e mentre il PC è in uso. Lo stato della chat viene verificato senza acquisire un turno: dati incompleti o incompatibili non vengono interpretati come inattività.
- Stato esplicito per consegne precedenti ancora in verifica, anche dopo la modifica di una programmazione. «Ultima consegna» mostra soltanto eventi con ricevuta; accodamento e ricezione restano distinti.
- Azione con conferma per ripartire dai soli invii futuri dopo un'incertezza storica. Conserva il vecchio tentativo senza segnarlo come ricevuto, insieme a testo, pause, bozze e ultima conferma.
- Lettura delle ricevute Claude compatibile con record di output fino a 16 MiB, con scansione limitata per ciclo e avanzamento persistente. Dati correlati non validi non possono confermare una consegna.
- Verifica preventiva delle autorizzazioni nel solo mittente Claude, senza dialoghi né preapprovazioni. Permessi e limitazioni del destinatario restano vincolanti; le combinazioni non qualificate sono indicate esplicitamente.
- Qualifica dell'installer sul runner Windows: installazione reale, apertura delle sezioni dell'interfaccia, supervisore in pausa, runtime incorporato, asset e disinstallazione con conservazione dei dati personali.

## 0.2.21

- Il browser incorporato in Codex non può più sostituire il titolo della chat con quello della pagina web aperta. Questo eliminava il riconoscimento di una chat già selezionata e provocava tentativi di selezione ripetuti senza inviare.
- Le letture e le azioni del mittente sono limitate al documento nativo verificato: campi e pulsanti delle pagine incorporate restano esclusi. Le finestre che espongono solo un sito non contano come una seconda chat.
- Verificato un invio reale del timer di produzione a Regia mentre risultava attiva e il browser incorporato era aperto, con ricevuta autentica e avanzamento della scadenza di 30 minuti.

## 0.2.20

- Elenco Codex letto dall'indice locale in sola lettura, con ID e date: non dipende più dai pulsanti della barra laterale o dalle sole chat visibili. Le sessioni interne degli agenti sono escluse. Gli errori dell'indice e dell'interfaccia vengono segnalati esplicitamente.
- Riconoscimento del campo messaggio anche attraverso ruolo, struttura multilinea, documento nativo e comandi del compositore. Il nuovo segnaposto «Lavora con ChatGPT» non impedisce più la preparazione. Campi multipli, documenti incorporati e bozze personali restano protetti.
- Errori di invio persistenti nel registro e nella dashboard, anche con una prenotazione già aperta: un'interfaccia non riconosciuta non viene mostrata come una normale preparazione o come un elenco vuoto.
- Preservati timer, versioni delle programmazioni e prenotazioni; la consegna resta confermata esclusivamente dal messaggio utente autentico nella cronologia della chat.
- Una prenotazione di una versione precedente, ancora ferma prima della preparazione, non blocca la nuova scadenza: viene conservata come annullata prima dell'invio. Le bozze già preparate e gli invii incerti non vengono ritentati né cancellati.
- Se la chat viene archiviata dopo l'invio e lo storico viene spostato, la ricevuta viene cercata nel percorso dell'identico ID, in sola lettura. L'archiviazione non autorizza nuovi invii.

## 0.2.19

- Ripristinato l'elenco delle chat dopo l'aggiornamento di Codex che ha sostituito il controllo «Archivia chat» con «Azioni della chat». Sono supportati entrambi i controlli, in italiano e inglese.
- Conservati progetto, stato, ID e associazione dei metadati, senza confondere le azioni del progetto con quelle di una chat. Le righe ripetute fra progetto e Recenti restano unificate.
- Verificata la lettura dell'elenco reale e il ritorno delle chat nel filtro «Ultime 24 ore»; aggiunta una regressione per la nuova struttura della barra laterale.

## 0.2.18

- Identificazione delle finestre Claude separate tramite l'ID nel registro dei popout dell'applicazione, con verifica del processo, del documento radice e del riquadro nativo. Nessun riconoscimento basato sul solo nome.
- Rifiuto di documenti incorporati, finestre duplicate, geometrie non corrispondenti e configurazioni non qualificate. Il caso supportato è attualmente la scala dello schermo al 100%.
- Una finestra separata non identificabile è dichiarata come controllo parziale e non riceve azioni automatiche.
- Le finestre vuote non sovrascrivono l'esito del controllo di una chat verificata. Titoli lunghi e ordine delle finestre non alterano la selezione del destinatario; i pulsanti delle pagine incorporate sono esclusi dalla navigazione.
- Dopo una rinomina Claude può conservare il vecchio nome del documento: il nome aggiornato viene letto dalla barra nativa della sessione, mantenendo il riconoscimento tramite ID. Le intestazioni dei messaggi e le pagine incorporate non possono sostituirlo.
- Verificati risposta automatica reale in una finestra Claude separata e riconoscimento dello stesso ID dopo una rinomina effettiva. Verificato anche il percorso completo del consenso nella chat desktop Codex: una sola attivazione di Sentinella, scrittura del file e controllo dei byte. Due collaudi aggiuntivi del protocollo Codex sono eseguiti in sessioni temporanee senza finestre.

## 0.2.17

- Le domande Codex vengono riconosciute anche quando il pulsante Invia è inizialmente disabilitato perché manca ancora il testo. Sentinella può compilare il messaggio di assenza e inviarlo solo dopo aver verificato che il pulsante sia abilitato.
- Restano esclusi campi disabilitati o nascosti e schede prive della struttura della domanda. Aggiunti controlli di regressione sul riconoscimento iniziale; verificata anche una risposta reale del programma nella chat di collaudo.

## 0.2.16

- Corretto il riconoscimento con più finestre Claude: destinatario individuato tramite ID e campo di composizione nativo, escludendo pannelli accessori e pagine incorporate. Domande e consensi sono limitati al documento nativo verificato.
- Errori di consegna persistenti e visibili, con tentativi distanziati, senza azzerare timer, bozze o ultimo invio. Un'attesa di ricevuta o un blocco su una programmazione non impedisce il controllo delle altre.
- Prenotazioni concorrenti protette da vincoli e aggiornamenti atomici. Una mancata lettura prima del clic conserva la bozza; un risultato incerto dopo il clic richiede ricevuta e non causa reinvii automatici.
- Osservazione passiva dell'origine degli input per evitare che gli eventi marcati da Windows come sintetici azzerino l'inattività. Ripiego conservativo se l'osservazione non è affidabile; nessuna memorizzazione di tasti, testo o coordinate.
- Collaudi aggiuntivi di finestre multiple, bozze estranee, pause, presenza al PC, ricevute parziali, concorrenza, riavvii e 100 cicli simulati consecutivi. Le simulazioni restano distinte dalle consegne reali e dai limiti operativi documentati.

## 0.2.15

- Nuova opzione **Approva automaticamente i comandi** in Impostazioni, separata dai consensi sui file e disattivata per default. Usa esclusivamente il consenso singolo; l'interfaccia avverte che può autorizzare cancellazioni e operazioni irreversibili anche fuori dalle cartelle delegate.
- Verifica del documento nativo, nuova lettura della richiesta e dell'opzione prima del clic, rispetto di pausa e inattività. L'attivazione rende subito dovuto il controllo, senza attendere il successivo intervallo completo.
- Le richieste di consenso non vengono confuse con domande a cui rispondere con il messaggio di assenza. Il controllo chat espone il motivo quando serve un consenso manuale.
- Collaudo Windows facoltativo su una finestra di prova innocua: consenso singolo, un solo clic registrato e prosecuzione verificata tramite file marcatore. Non esegue comandi delle chat reali.

## 0.2.14

- Destinatari riconosciuti tramite ID stabile per Codex e Claude. I nomi aggiornati vengono risolti dall'ID; un ID assente, archiviato o ambiguo non viene sostituito con un'altra chat omonima.
- Migrazione delle programmazioni esistenti con backup, preservando ID della programmazione, versione, pausa, scadenze, messaggi e prenotazioni. Per Claude viene privilegiata l'identità già documentata nelle ricevute o nelle bozze prenotate.
- Verifica della chat Claude aperta tramite l'ID nell'URL del documento nativo; le pagine del browser integrato non vengono confuse con la sessione.
- Salvataggi coordinati per conservare gli ID anche quando un editor era aperto prima della migrazione o della rinomina. Ricevute e bozze restano collegate alla stessa sessione dopo una rinomina.

## 0.2.13

- Claude: le etichette Aperta/Chiusa/Open/Closed e il conteggio delle pull request non nascondono più una chat esistente al riconoscimento del destinatario. Restano obbligatori titolo esatto, progetto e assenza di omonimi ambigui.
- Agenda: stati, attività e testi rimangono completi, senza puntini di troncamento. I testi vanno a capo e le righe crescono quanto necessario, mantenendo visibili i comandi e lo scorrimento.

## 0.2.12

- Panoramica con quattro card per notifiche, controllo chat, risposte automatiche e recupero delle scritture: opzioni abilitate, pausa, attesa di inattività e controlli non aggiornati restano distinti.
- Card su quattro o due colonne secondo lo spazio disponibile, con ultima attività, frequenza e collegamento alle opzioni; agenda e comandi restano accessibili anche con dieci programmazioni.
- Icona di Sentinella condivisa da finestra, barra delle applicazioni, eseguibile, installer e collegamenti; l'avvio dai sorgenti non usa più l'icona di Python. Collegamento desktop opzionale nell'installer.

## 0.2.11

- I pannelli informativi, come Utilizzo di Claude, e i dialoghi nascosti non bloccano più gli invii programmati. Il controllo verifica visibilità, modalità e controlli interni: richieste reali e stati sconosciuti restano protetti.
- Il controllo dei dialoghi viene ripetuto prima di Invia; le schede di domande nascoste o disabilitate non vengono considerate richieste attive.
- Il registro indica il nome del dialogo che rinvia un invio. Aggiunta la diagnosi in sola lettura `--inspect-send-state`, senza lettura delle bozze o azioni sulla chat.

## 0.2.10

- Ricevute Claude riconosciute anche quando il messaggio umano è accodato durante il lavoro insieme ad avvisi di sistema e l'evento di accodamento non contiene testo. Restano obbligatori testo esatto, sessione, orario e identificativi correlati dell'assorbimento.
- Pulizia dei residui di Sentinella già ricevuti anche quando Claude rimuove lo spazio finale, con copia integrale prima della pulizia; bozze personali e testi modificati restano protetti.
- Una validazione interrotta dalla digitazione prima del pulsante Invia conserva la preparazione e riprende senza duplicare la prenotazione.
- Stato Bozza nella chat distinto dal salvataggio della programmazione.

## 0.2.9

- Revisione per la pubblicazione: rimosse anteprime e azioni non più raggiungibili, con meno letture periodiche delle configurazioni nell'editor.
- Icone condivise e riuso dei font dell'agenda; abbreviazione dei testi lunghi con ricerca binaria invece di misurazioni ripetute per ogni carattere.
- Test indipendenti dai dati del PC dello sviluppatore, cache delle dipendenze in CI e build con polling breve e limite complessivo per macchine più lente.
- Comando Nuova programmazione coerente anche in dashboard.

## 0.2.8

- Agenda con informazioni principali allineate sulla stessa riga e dettagli sulla seconda; i testi lunghi restano accessibili nei tooltip.
- Colonna App come prima colonna delle programmazioni salvate, con Codex o Claude e azioni ancora sulla destra.

## 0.2.7

- Conferme di eliminazione con annullamento predefinito, nome e contenuto della raccolta; cancellazione della raccolta e dei suoi messaggi soltanto dopo conferma, senza cambiare programmazioni esistenti.
- Aggiornamento immediato di elenco, filtro, selettori ed editor dopo la rimozione di una raccolta, senza richieste spurie di salvataggio.
- Ripristino esplicito e additivo dei messaggi predefiniti eliminati, preservando i testi personalizzati e senza far riapparire elementi cancellati durante le normali letture.
- Messaggi salvati senza comando Usa nella programmazione e senza frase sul mancato avvio degli invii. Il riuso resta disponibile dall'editor delle programmazioni.
- Opzioni di invio su quattro colonne con titoli e controlli allineati, nello stile nativo dell'applicazione.

## 0.2.6

- Salvataggi uniformi per programmazioni, raccolta e tempi dei controlli: Salva modifiche o Scarta modifiche; chiusura dell'avviso ed Esc mantengono l'editor. La protezione comprende cambio pagina, cambio elemento e chiusura dell'app.
- Gli interruttori salvati subito non salvano più i tempi ancora in modifica. L'esportazione nella raccolta usa una finestra dedicata e non salva o attiva la programmazione.
- Comandi Pausa/Riprendi con icona e testo, più cestino con conferma, a destra di ogni programmazione. Eliminare una programmazione conserva cronologia, bozze e prenotazioni incerte.
- Clic sul nome della chat in Panoramica per modificare, senza menu laterale. Frequenza e tre opzioni su quattro colonne; selezione chat iniziale limitata alle ultime 24 ore.
- Dodici messaggi predefiniti riscritti per proseguire il lavoro già autorizzato durante l'assenza dell'utente, preservando domande e permessi mancanti senza bloccare le attività indipendenti.
- Pulizia completa del registro invii e delle notifiche dai rispettivi filtri, con conferma e cancellazione dei file associati già conclusi. Timer, configurazioni, bozze e prove operative contro i duplicati restano conservati.

## 0.2.5

- Modifica delle programmazioni direttamente sotto l’elenco, senza aprire il percorso di creazione. Editor unico, protezione delle modifiche non salvate e comandi riuniti in basso.
- Scelta dei messaggi salvati con anteprima contenuta nel bordo e pulsante Usa a destra; intestazioni degli elenchi allineate ai dati.
- Con invio durante uso PC attivo, selezione della chat esatta nell’app in background. L’app in uso su un’altra chat e le consegne incerte mantengono motivi distinti.
- Ricevute Claude durante il lavoro correlate anche con uno scarto di pochi millisecondi, conservando identità, testo e correlazione univoca degli eventi umani.
- Identità esplicita di Sentinella per l’icona nella barra delle applicazioni Windows, coerente con il collegamento dell’installer.

## 0.2.4

- Messaggi salvati con ricerca nel campo, comandi delle raccolte espliciti e azioni Usa, Elimina e Salva riunite in basso a destra.
- Terminologia uniforme per i messaggi, senza footer descrittivi ripetuti; nuovo messaggio accessibile dall’editor e feedback vicino ai campi.
- Dashboard con stato In attesa, motivo PC in uso e indicazione di ripresa automatica, senza richiedere di aprire o cambiare chat.

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
