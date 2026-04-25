"""
Prompt strutturato per Claude vision: estrazione dati da scorecard golf italiana.

Il prompt è in italiano per due motivi:
1. Le scorecard sono in italiano, e i nomi dei campi (Buca, Par, Ordine colpi,
   Percorso, ecc.) sono italiani — il modello fa meno errori di mapping se
   tutto è nella stessa lingua.
2. Le note/warnings che il modello aggiunge nel JSON saranno mostrate
   all'utente NETGOLF, che parla italiano.

Tenuto in un file dedicato così è facile iterarci sopra senza toccare il
codice della rotta o del client.
"""

PROMPT_OCR_SCORECARD = """\
Sei un assistente che estrae dati strutturati da scorecard di gare di golf \
italiane. Ti verrà mostrata UNA foto di una scorecard. Devi restituire \
ESCLUSIVAMENTE un oggetto JSON valido (nessun testo prima o dopo, nessun \
markdown, nessun ```json fences) con la seguente struttura:

{
  "torneo": {
    "nome": str | null,                  // es. "TROFEO NASHI ARGAN"
    "data_gara": str | null,             // formato ISO "YYYY-MM-DD"
    "giro": str | null,                  // es. "1° Giro"
    "ora_partenza": str | null,          // formato "HH:MM"
    "tee_partenza": str | null,          // es. "1" o "10"
    "pin_position": str | null,          // es. "A", "B", "C", "D"
    "passaggio_buca_10": str | null,     // formato "HH:MM"
    "arrivo_buca_18": str | null         // formato "HH:MM"
  },
  "giocatore": {
    "tessera": str | null,               // numero tessera FIG es. "23094"
    "nome_completo": str | null          // es. "COLOMBO MARCO"
  },
  "campo": {
    "circolo": str | null,               // es. "ARZAGA"
    "percorso": str | null,              // es. "Jack Nicklaus 1-18"
    "percorso_codice_stampato": str | null,  // codice abbreviato sulla card es. "JNI 1-18"
    "tee_colore": str | null,            // es. "Giallo", "Bianco", "Rosso"
    "par_totale": int | null,            // es. 72
    "cr_uomini": float | null,           // Course Rating es. 71.7
    "sr_uomini": int | null,             // Slope Rating es. 126
    "cr_donne": float | null,
    "sr_donne": int | null
  },
  "handicap": {
    "hcp_index": float | null,           // es. 16.9
    "hcp_gioco": int | null,             // es. 19
    "categoria": str | null              // es. "1ª Cat", "2ª Cat", "3ª Cat", "4ª Cat", "5ª Cat"
  },
  "buche": [
    // ESATTAMENTE 18 oggetti, uno per buca, in ordine da 1 a 18
    {
      "buca": int,                       // 1..18
      "par": int | null,                 // par stampato sulla scorecard, 3/4/5
      "metri_uomini": int | null,        // distanza in metri tee uomini
      "metri_donne": int | null,
      "ordine_colpi": int | null,        // handicap stroke index, da 1 a 18, NO duplicati
      "score": int | "X" | null,         // valore scritto a mano nella colonna giocatore
      "score_confidence": "high" | "medium" | "low",  // tua confidenza nella lettura
      "note_score": str | null           // note su correzioni/cancellature/incertezze
    }
  ],
  "totali_stampati": {
    "out_par": int | null,               // somma par buche 1-9 (di solito 36)
    "in_par": int | null,                // somma par buche 10-18
    "totale_par": int | null,            // di solito 72
    "out_metri_uomini": int | null,
    "in_metri_uomini": int | null,
    "totale_metri_uomini": int | null
  },
  "warnings": [
    // lista di stringhe in italiano. Includi qui ogni problema che hai notato:
    // - score con cancellature ambigue
    // - duplicati nell'ordine_colpi (deve essere permutazione 1..18)
    // - somma score buche 1-9 != OUT stampato
    // - foto sfocata su una buca specifica
    // - X o pickup
    // - qualsiasi altra anomalia che richiede verifica umana
  ]
}

REGOLE IMPORTANTI:
1. La colonna "Player" (NON "Marker") contiene gli score del giocatore \
intestatario della scorecard. Estrai SEMPRE da quella colonna, mai dalla \
colonna "Marker" (che contiene gli score di un altro giocatore di cui questo \
è il marcatore).
2. Se vedi "X" nella casella score, mettila come stringa "X" (significa \
"no return" / pickup) e aggiungi un warning.
3. Se non riesci a leggere uno score con sicurezza, mettilo lo stesso ma \
imposta "score_confidence": "low" e aggiungi un warning specifico.
4. L'array "buche" deve avere SEMPRE 18 elementi anche se alcuni campi sono \
null. Buca 1 al primo posto, buca 18 all'ultimo.
5. Per le date in italiano: "sabato 11 aprile 2026" → "2026-04-11".
6. Per i numeri decimali con virgola italiana: "16,9" → 16.9 (float).
7. Non inventare dati. Se un campo non è leggibile o non è presente, usa null.
8. Restituisci SOLO il JSON, nessun testo aggiuntivo, nessun commento, \
nessun markdown.
"""
