"""Safe starter messages: pure data, no scheduling, permissions or sends."""
import uuid
from message_templates import AUTONOMOUS_CONTINUATION

# Exact old values support an opt-in, in-memory upgrade of untouched seeds.
# Match ID, title, text and updated_at == 0; never replace a customization.
LEGACY_TEMPLATE_TEXTS_BY_ID = {'7d09dc3e-edbc-5a02-b45c-efe687db1a87': {'title': 'Riprendi il lavoro concordato',
                                          'text': 'Riprendi il lavoro già concordato, rispettando il '
                                                  'perimetro e le autorizzazioni esistenti. Se manca '
                                                  'un’informazione indispensabile, indica il punto da '
                                                  'chiarire prima di procedere.'},
 '9f1a4021-7b0b-51bc-98c6-3470b55d553c': {'title': 'Controlla il punto di arresto',
                                          'text': 'Controlla dove si è fermata l’attività concordata e '
                                                  'descrivi il prossimo passo utile. Conserva le modifiche e '
                                                  'i dati già presenti.'},
 '5c3f4371-9c14-50ba-8e9c-5fcb98defc4b': {'title': 'Segnala gli impedimenti',
                                          'text': 'Riepiloga gli impedimenti attuali del lavoro concordato, '
                                                  'distinguendo ciò che puoi risolvere entro il perimetro '
                                                  'autorizzato dalle decisioni che richiedono una risposta.'},
 '81d99e7b-5581-5467-94a6-b4097f791fd0': {'title': 'Stato del lavoro',
                                          'text': 'Prepara un breve aggiornamento sul lavoro concordato: '
                                                  'cosa è stato completato, cosa resta da fare e quali '
                                                  'verifiche sono state davvero eseguite.'},
 'bf443db4-4a72-57c4-9fcb-dd276ef15783': {'title': 'Punto per la ripresa',
                                          'text': 'Lascia un punto di ripresa chiaro con i risultati '
                                                  'disponibili, i file pertinenti e le questioni ancora '
                                                  'aperte. Distingui i fatti verificati dalle ipotesi.'},
 'd331696b-6e03-5a6f-9425-5685dfe2b70b': {'title': 'Risultati e prossimi passi',
                                          'text': 'Riepiloga i risultati raggiunti e proponi i prossimi '
                                                  'passi, senza avviare nuove attività al di fuori di quelle '
                                                  'già autorizzate.'},
 'ea14e7c7-f586-5ff5-a04a-3469389f6674': {'title': 'Rivedi le modifiche',
                                          'text': 'Rivedi le modifiche del lavoro concordato e segnala '
                                                  'eventuali errori o incoerenze. Conserva il lavoro in '
                                                  'corso e rispetta i limiti delle verifiche autorizzate.'},
 'ff38ba1b-1bef-5d22-b653-319acddec9ec': {'title': 'Verifiche pertinenti',
                                          'text': 'Individua le verifiche pertinenti per il lavoro '
                                                  'concordato ed esegui solo quelle già autorizzate. Riporta '
                                                  'esito, limiti e problemi ancora aperti senza dichiarare '
                                                  'riuscite prove non svolte.'},
 '162cb133-2c53-5942-92c8-fd4ba1977387': {'title': 'Controlla la consegna',
                                          'text': 'Controlla che la consegna del lavoro concordato comprenda '
                                                  'i risultati richiesti e le prove disponibili. Evidenzia '
                                                  'ciò che manca ancora prima di considerarla completa.'},
 'd54b7413-420e-57e3-9677-45145d6a051e': {'title': 'Ordina le priorità',
                                          'text': 'Ordina le attività già concordate per priorità e '
                                                  'dipendenze. Proponi una sequenza concreta, senza '
                                                  'modificare impostazioni o ampliare il perimetro di '
                                                  'lavoro.'},
 '00e345a0-ecfd-5a60-acc1-0f17f72a9e2d': {'title': 'Prepara il prossimo passo',
                                          'text': 'Descrivi il prossimo passo del lavoro concordato, le '
                                                  'informazioni necessarie e il risultato atteso. Segnala '
                                                  'eventuali decisioni ancora da prendere.'},
 'b3a8c56b-8f3d-5eb2-8bdd-582eec649fee': {'title': 'Piano di verifica',
                                          'text': 'Prepara un piano breve per verificare il lavoro '
                                                  'concordato: controlli necessari, evidenze da conservare e '
                                                  'condizioni per considerarlo completo. Non avviare nuove '
                                                  'attività senza l’autorizzazione prevista.'}}

_INTENTS = (('Avanzamento',
  (('Riprendi il lavoro concordato',
    'Riprendi dal punto raggiunto ed esegui i passi necessari a completare l’attività concordata. Quando '
    'risolvi un impedimento, passa alla successiva attività già autorizzata e verifica i risultati.'),
   ('Controlla il punto di arresto',
    'Ricostruisci il punto di arresto usando dati e modifiche presenti, recupera i problemi risolvibili nel '
    'perimetro autorizzato e riprendi l’esecuzione fino al completamento.'),
   ('Segnala gli impedimenti',
    'Individua gli impedimenti, risolvi quelli che rientrano nelle autorizzazioni esistenti e continua il '
    'lavoro. Registra i punti che dipendono da informazioni o permessi mancanti e porta avanti le altre '
    'attività già autorizzate.'))),
 ('Riepiloghi',
  (('Stato del lavoro',
    'Registra un aggiornamento sintetico con risultati verificati e attività ancora aperte, quindi continua '
    'l’esecuzione dei passi già autorizzati fino a completarli.'),
   ('Punto per la ripresa',
    'Mantieni un punto di ripresa con risultati, file pertinenti e questioni aperte; aggiornalo mentre '
    'prosegui concretamente il lavoro già autorizzato fino al completamento.'),
   ('Risultati e prossimi passi',
    'Riepiloga brevemente i risultati e passa ai successivi passi già autorizzati, seguendo le dipendenze '
    'fino a completare il lavoro.'))),
 ('Qualità',
  (('Rivedi le modifiche',
    'Rivedi le modifiche, correggi gli errori e le incoerenze che rientrano nel lavoro già autorizzato, '
    'esegui le verifiche pertinenti già consentite e porta a termine le attività rimanenti.'),
   ('Verifiche pertinenti',
    'Esegui le verifiche pertinenti già autorizzate, correggi i problemi risolvibili entro il perimetro '
    'concordato e continua fino a ottenere risultati verificati. Registra limiti e prove svolte senza '
    'presentare come eseguiti controlli mancanti.'),
   ('Controlla la consegna',
    'Completa gli elementi mancanti della consegna già autorizzata, verifica risultati e prove disponibili e '
    'risolvi i problemi recuperabili. Concludi con una consegna chiara quando il lavoro concordato è '
    'effettivamente terminato.'))),
 ('Pianificazione',
  (('Ordina le priorità',
    'Ordina per priorità e dipendenze le attività già concordate e avviane l’esecuzione in quella sequenza. '
    'Completa i passi eseguibili entro le autorizzazioni esistenti e verifica i risultati.'),
   ('Prepara il prossimo passo',
    'Identifica il prossimo passo eseguibile del lavoro già autorizzato, svolgilo e passa al successivo fino '
    'al completamento. Quando un passo dipende da una decisione mancante, annotalo e continua i passi '
    'indipendenti già autorizzati.'),
   ('Piano di verifica',
    'Organizza i controlli necessari per il lavoro già concordato ed esegui quelli già autorizzati. Correggi '
    'i problemi risolvibili, conserva le evidenze e completa le attività rimanenti fino alla consegna.'))))

TEMPLATES = tuple((name, tuple((title, intent + "\n\n" + AUTONOMOUS_CONTINUATION)
                             for title, intent in templates)) for name, templates in _INTENTS)


def starter_library():
    collections, items = [], []
    for name, templates in TEMPLATES:
        collection_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'sentinella://templates/v1/' + name))
        collections.append({'id': collection_id, 'name': name})
        for title, text in templates:
            items.append({'id': str(uuid.uuid5(uuid.NAMESPACE_URL, 'sentinella://templates/v1/' + name + '/' + title)),
                          'title': title, 'text': text, 'updated_at': 0,
                          'collection_id': collection_id})
    return {'schema_version': 1, 'collections': collections, 'items': items}
