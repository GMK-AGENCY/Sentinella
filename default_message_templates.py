"""Italian starter texts. Pure data: never starts a task or sends a message."""
import uuid


TEMPLATES = (
    ('Avanzamento', (
        ('Riprendi il lavoro concordato', 'Riprendi il lavoro già concordato, rispettando il perimetro e le autorizzazioni esistenti. Se manca un’informazione indispensabile, indica il punto da chiarire prima di procedere.'),
        ('Controlla il punto di arresto', 'Controlla dove si è fermata l’attività concordata e descrivi il prossimo passo utile. Conserva le modifiche e i dati già presenti.'),
        ('Segnala gli impedimenti', 'Riepiloga gli impedimenti attuali del lavoro concordato, distinguendo ciò che puoi risolvere entro il perimetro autorizzato dalle decisioni che richiedono una risposta.'),
    )),
    ('Riepiloghi', (
        ('Stato del lavoro', 'Prepara un breve aggiornamento sul lavoro concordato: cosa è stato completato, cosa resta da fare e quali verifiche sono state davvero eseguite.'),
        ('Punto per la ripresa', 'Lascia un punto di ripresa chiaro con i risultati disponibili, i file pertinenti e le questioni ancora aperte. Distingui i fatti verificati dalle ipotesi.'),
        ('Risultati e prossimi passi', 'Riepiloga i risultati raggiunti e proponi i prossimi passi, senza avviare nuove attività al di fuori di quelle già autorizzate.'),
    )),
    ('Qualità', (
        ('Rivedi le modifiche', 'Rivedi le modifiche del lavoro concordato e segnala eventuali errori o incoerenze. Conserva il lavoro in corso e rispetta i limiti delle verifiche autorizzate.'),
        ('Verifiche pertinenti', 'Individua le verifiche pertinenti per il lavoro concordato ed esegui solo quelle già autorizzate. Riporta esito, limiti e problemi ancora aperti senza dichiarare riuscite prove non svolte.'),
        ('Controlla la consegna', 'Controlla che la consegna del lavoro concordato comprenda i risultati richiesti e le prove disponibili. Evidenzia ciò che manca ancora prima di considerarla completa.'),
    )),
    ('Pianificazione', (
        ('Ordina le priorità', 'Ordina le attività già concordate per priorità e dipendenze. Proponi una sequenza concreta, senza modificare impostazioni o ampliare il perimetro di lavoro.'),
        ('Prepara il prossimo passo', 'Descrivi il prossimo passo del lavoro concordato, le informazioni necessarie e il risultato atteso. Segnala eventuali decisioni ancora da prendere.'),
        ('Piano di verifica', 'Prepara un piano breve per verificare il lavoro concordato: controlli necessari, evidenze da conservare e condizioni per considerarlo completo. Non avviare nuove attività senza l’autorizzazione prevista.'),
    )),
)


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
