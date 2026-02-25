# API Contract

Specifica degli endpoint previsti per il backend FastAPI di **desktop-order-system**.

> **Stato**: Design-only. Nessun endpoint è ancora implementato.  
> Il backend risiederà in `backend/app/` e condividerà il database SQLite con il client desktop.

---

## Indice

1. [Convenzioni generali](#1-convenzioni-generali)
2. [Autenticazione](#2-autenticazione)
3. [Formato errori](#3-formato-errori)
4. [Endpoints](#4-endpoints)
   - [GET /health](#get-health)
   - [GET /skus/lookup-ean/{ean}](#get-skupslookup-eanean)
   - [GET /stock](#get-stock)
   - [GET /stock/{sku}](#get-stocksku)
   - [POST /exceptions](#post-exceptions)
   - [POST /receipts/close](#post-receiptsclose)
5. [Regole di idempotenza](#5-regole-di-idempotenza)
6. [Validazione e vincoli](#6-validazione-e-vincoli)

---

## 1. Convenzioni generali

| Proprietà | Valore |
|---|---|
| Base URL (sviluppo) | `http://127.0.0.1:8000/api/v1` |
| Formato corpo | `application/json` (UTF-8) |
| Date | ISO 8601 `YYYY-MM-DD` (stringa) |
| Quantità | Intero ≥ 0 nel payload (il segno viene assegnato internamente in base all'event type) |
| Paginazione | Query param `?page=1&page_size=50` dove rilevante |
| Versioning | Prefisso `/api/v1`; breaking changes → nuovo prefisso `/api/v2` |

**Header richiesti su ogni chiamata autenticata:**

```
Authorization: Bearer <DOS_API_TOKEN>
Content-Type: application/json
```

---

## 2. Autenticazione

Bearer token statico configurato via `DOS_API_TOKEN` (vedi [docs/config.md](config.md)).

- Token assente o errato → `401 Unauthorized`
- Endpoint `/health` è **pubblico** (no token richiesto)

---

## 3. Formato errori

Tutti gli errori seguono questa struttura unificata:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Descrizione leggibile del problema",
    "details": [
      {
        "field": "qty",
        "issue": "Deve essere un intero >= 1"
      }
    ]
  }
}
```

### Codici HTTP standard

| HTTP | `error.code` | Quando |
|---|---|---|
| `400` | `VALIDATION_ERROR` | Campo mancante, tipo errato, valore fuori range |
| `401` | `UNAUTHORIZED` | Token assente o non valido |
| `404` | `NOT_FOUND` | SKU, receipts o risorsa inesistente |
| `409` | `CONFLICT` | Risorsa già esistente (violazione idempotency key) |
| `422` | `BUSINESS_RULE_ERROR` | Vincolo di dominio violato (es. EAN non valido) |
| `500` | `INTERNAL_ERROR` | Errore non gestito lato server |

---

## 4. Endpoints

---

### GET /health

Verifica che il servizio sia attivo e raggiungibile.

**Autenticazione**: nessuna

#### Response `200 OK`

```json
{
  "status": "ok",
  "version": "1.0.0",
  "db_path": "/path/to/data/app.db",
  "db_reachable": true,
  "timestamp": "2026-02-24T10:30:00Z"
}
```

#### Response `503 Service Unavailable` (DB non raggiungibile)

```json
{
  "status": "degraded",
  "version": "1.0.0",
  "db_path": "/path/to/data/app.db",
  "db_reachable": false,
  "timestamp": "2026-02-24T10:30:00Z"
}
```

---

### GET /skus/lookup-ean/{ean}

Cerca un SKU tramite codice EAN/GTIN. Utile per il client Android (scansione barcode).

**Parametri path**:

| Param | Tipo | Note |
|---|---|---|
| `ean` | `string` | Codice EAN-8, EAN-13 o GTIN-14. Non URL-encoded. |

#### Response `200 OK`

```json
{
  "sku": "PRD-0042",
  "description": "Latte intero UHT 1L",
  "ean": "8001234567890",
  "ean_valid": true,
  "moq": 6,
  "pack_size": 6,
  "lead_time_days": 3,
  "safety_stock": 12,
  "shelf_life_days": 90,
  "in_assortment": true,
  "category": "DAIRY",
  "department": "FRESH"
}
```

#### Response `404 Not Found`

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Nessun SKU trovato per EAN '8001234567890'",
    "details": []
  }
}
```

#### Response `422 Unprocessable Entity` (EAN non valido)

```json
{
  "error": {
    "code": "BUSINESS_RULE_ERROR",
    "message": "EAN '123' non supera la verifica del check digit",
    "details": [
      { "field": "ean", "issue": "Lunghezza non valida (attesa: 8, 13 o 14 cifre)" }
    ]
  }
}
```

> **Nota**: EAN non valido viene **loggato** ma non fa crashare la lookup; il campo `ean_valid: false` viene restituito nel profilo SKU se l'EAN è già presente nel DB ma malformato.

---

### GET /stock

Lista stock calcolato AsOf per tutti gli SKU (o un sottoinsieme filtrato).

**Query params**:

| Param | Tipo | Default | Note |
|---|---|---|---|
| `asof` | `string` (date) | oggi | Data di calcolo stock (es. `2026-02-24`) |
| `sku` | `string` (ripetibile) | tutti | Filtra su uno o più SKU: `?sku=PRD-001&sku=PRD-002` |
| `in_assortment` | `boolean` | `true` | `false` per includere SKU dismessi |
| `page` | `integer` | `1` | Pagina (1-based) |
| `page_size` | `integer` | `50` | Righe per pagina (max 200) |

#### Response `200 OK`

```json
{
  "asof": "2026-02-24",
  "page": 1,
  "page_size": 50,
  "total": 2,
  "items": [
    {
      "sku": "PRD-0042",
      "description": "Latte intero UHT 1L",
      "on_hand": 48,
      "on_order": 24,
      "last_event_date": "2026-02-22"
    },
    {
      "sku": "PRD-0055",
      "description": "Yogurt bianco 125g",
      "on_hand": 0,
      "on_order": 0,
      "last_event_date": null
    }
  ]
}
```

---

### GET /stock/{sku}

Stock calcolato AsOf per un singolo SKU, con dettaglio eventi.

**Parametri path**: `sku` — codice SKU esatto (case-sensitive)

**Query params**:

| Param | Tipo | Default |
|---|---|---|
| `asof` | `string` (date) | oggi |

#### Response `200 OK`

```json
{
  "sku": "PRD-0042",
  "description": "Latte intero UHT 1L",
  "asof": "2026-02-24",
  "on_hand": 48,
  "on_order": 24,
  "last_event_date": "2026-02-22",
  "recent_transactions": [
    {
      "transaction_id": 1042,
      "date": "2026-02-22",
      "event": "RECEIPT",
      "qty": 24,
      "receipt_date": "2026-02-22",
      "note": "OC-20260220-001"
    },
    {
      "transaction_id": 1031,
      "date": "2026-02-21",
      "event": "SALE",
      "qty": 12,
      "receipt_date": null,
      "note": ""
    }
  ]
}
```

#### Response `404 Not Found`

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "SKU 'PRD-9999' non trovato",
    "details": []
  }
}
```

---

### POST /exceptions

Registra un evento di eccezione nel ledger (WASTE, ADJUST, UNFULFILLED).

**Idempotency key**: `date + sku + event` — se esiste già una transazione con la stessa tripletta nella stessa giornata, la richiesta viene **respinta con `409`** (non inserita due volte).

#### Request body

```json
{
  "date": "2026-02-24",
  "sku": "PRD-0042",
  "event": "WASTE",
  "qty": 3,
  "note": "Prodotti scaduti trovati in reparto"
}
```

| Campo | Tipo | Obbligatorio | Valori ammessi |
|---|---|---|---|
| `date` | `string` (date) | ✓ | ISO 8601, non futura di oltre 7 giorni |
| `sku` | `string` | ✓ | SKU esistente nel DB |
| `event` | `string` | ✓ | `WASTE`, `ADJUST`, `UNFULFILLED` |
| `qty` | `integer` | ✓ | ≥ 1 (il segno viene assegnato internamente) |
| `note` | `string` | — | Max 500 caratteri |

#### Response `201 Created`

```json
{
  "transaction_id": 1105,
  "date": "2026-02-24",
  "sku": "PRD-0042",
  "event": "WASTE",
  "qty": 3,
  "note": "Prodotti scaduti trovati in reparto",
  "idempotency_key": "2026-02-24:PRD-0042:WASTE"
}
```

#### Response `400 Bad Request` (campo mancante)

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Il corpo della richiesta contiene campi non validi",
    "details": [
      { "field": "qty", "issue": "Campo obbligatorio mancante" },
      { "field": "event", "issue": "'SALE' non è un tipo di eccezione ammesso; valori validi: WASTE, ADJUST, UNFULFILLED" }
    ]
  }
}
```

#### Response `404 Not Found` (SKU inesistente)

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "SKU 'PRD-9999' non trovato nel database",
    "details": []
  }
}
```

#### Response `409 Conflict` (idempotency key già presente)

```json
{
  "error": {
    "code": "CONFLICT",
    "message": "Eccezione già registrata per questa tripletta date/sku/event",
    "details": [
      {
        "field": "idempotency_key",
        "issue": "Chiave '2026-02-24:PRD-0042:WASTE' già presente (transaction_id=1105)"
      }
    ]
  }
}
```

---

### POST /receipts/close

Chiude un ordine di acquisto registrando gli eventi RECEIPT nel ledger.  
Operazione **idempotente**: inviare la stessa `receipt_id` due volte produce il medesimo stato finale (la seconda chiamata restituisce `200` con `already_processed: true`).

#### Request body

```json
{
  "receipt_id": "2026-02-24_SUPPLIER-A_REC001",
  "receipt_date": "2026-02-24",
  "lines": [
    {
      "sku": "PRD-0042",
      "qty_received": 24,
      "note": "OC-20260220-001"
    },
    {
      "sku": "PRD-0055",
      "qty_received": 48,
      "note": "OC-20260220-001"
    }
  ]
}
```

| Campo | Tipo | Obbligatorio | Note |
|---|---|---|---|
| `receipt_id` | `string` | ✓ | Chiave di idempotenza. Formato consigliato: `{receipt_date}_{supplier}_{ref}`. Max 100 char. |
| `receipt_date` | `string` (date) | ✓ | Data effettiva di ricezione |
| `lines` | `array` | ✓ | Almeno 1 riga |
| `lines[].sku` | `string` | ✓ | SKU esistente |
| `lines[].qty_received` | `integer` | ✓ | ≥ 1 |
| `lines[].note` | `string` | — | Max 200 caratteri |

#### Response `201 Created` (prima elaborazione)

```json
{
  "receipt_id": "2026-02-24_SUPPLIER-A_REC001",
  "receipt_date": "2026-02-24",
  "already_processed": false,
  "lines": [
    {
      "sku": "PRD-0042",
      "qty_received": 24,
      "transaction_id": 1106,
      "status": "ok"
    },
    {
      "sku": "PRD-0055",
      "qty_received": 48,
      "transaction_id": 1107,
      "status": "ok"
    }
  ]
}
```

#### Response `200 OK` (già elaborato — idempotenza)

```json
{
  "receipt_id": "2026-02-24_SUPPLIER-A_REC001",
  "receipt_date": "2026-02-24",
  "already_processed": true,
  "lines": [
    {
      "sku": "PRD-0042",
      "qty_received": 24,
      "transaction_id": 1106,
      "status": "already_received"
    },
    {
      "sku": "PRD-0055",
      "qty_received": 48,
      "transaction_id": 1107,
      "status": "already_received"
    }
  ]
}
```

#### Response `400 Bad Request` (errori per riga)

Gli errori vengono riportati **riga per riga** senza interrompere la validazione delle altre righe:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Una o più righe contengono errori; nessuna riga è stata elaborata",
    "details": [
      {
        "field": "lines[1].sku",
        "issue": "SKU 'PRD-9999' non trovato nel database"
      },
      {
        "field": "lines[1].qty_received",
        "issue": "Deve essere un intero >= 1; ricevuto: -5"
      }
    ]
  }
}
```

> **Atomicità**: la richiesta è atomica — se anche una sola riga fallisce validazione, *nessuna* riga viene scritta nel ledger.

---

## 5. Regole di idempotenza

| Endpoint | Chiave di idempotenza | Comportamento duplicato |
|---|---|---|
| `POST /exceptions` | `date + sku + event` | `409 Conflict` |
| `POST /receipts/close` | `receipt_id` | `200 OK` con `already_processed: true` |

La chiave `receipt_id` deve essere **deterministica** e costruita dal chiamante prima di inviare la richiesta, in modo che un retry (es. dopo timeout di rete) non crei ricevute duplicate.

Formato consigliato per `receipt_id`:

```
{receipt_date}_{supplier_code}_{document_ref}
# Esempio: 2026-02-24_SUPPLIER-A_DDT-2024-00042
```

---

## 6. Validazione e vincoli

### Date

- Formato obbligatorio: `YYYY-MM-DD`
- Date future > 7 giorni rifiutate per eventi WASTE/ADJUST/UNFULFILLED
- `asof` per le query stock: accettata qualsiasi data passata o presente

### EAN

- Formati accettati: EAN-8 (8 cifre), EAN-13 (13 cifre), GTIN-14 (14 cifre)
- Verifica check digit eseguita lato API
- EAN malformato in lookup → `422` con dettaglio del problema
- EAN malformato già presente nel DB (legacy) → restituito con `ean_valid: false`, warning loggato, **mai crash**

### Quantità

- Sempre ≥ 1 nel payload (intero positivo)
- Il segno viene assegnato internamente in base all'event type:
  - WASTE, SALE → decremento `on_hand`
  - ADJUST → set assoluto `on_hand`
  - ORDER → incremento `on_order`
  - RECEIPT → decremento `on_order`, incremento `on_hand`

### Lunghezze stringa

| Campo | Max lunghezza |
|---|---|
| `sku` | 50 caratteri |
| `receipt_id` | 100 caratteri |
| `note` (eccezioni) | 500 caratteri |
| `note` (righe ricevuta) | 200 caratteri |

---

## Vedere anche

- [docs/config.md](config.md) — variabili d'ambiente del backend
- [docs/runbook.md](runbook.md) — avvio, backup, troubleshooting
- `src/domain/models.py` — definizione dei tipi EventType, SKU, Transaction, Stock
- `src/domain/ledger.py` — logica AsOf di calcolo stock
