# 📊 Dataset Demo - Desktop Order System

Dataset pulito per testare il sistema di riordino con **4 SKU** rappresentativi di tutte le categorie di variabilità della domanda.

## 📅 Caratteristiche Dataset

- **Periodo storico**: 18 Gennaio - 7 Febbraio 2026 (21 giorni)
- **SKU totali**: 4 (uno per ogni categoria di demand variability)
- **Inventory Position**: Calibrato per generare proposte d'ordine > 0 per tutti gli SKU

## 🏷️ SKU Inclusi

### 1. SKU_STABLE (STABLE - Vendite Stabili)
- **Descrizione**: Prodotto Vendite Stabili
- **Pattern vendite**: 10 unità/giorno costanti
- **Stock attuale**: 80 unità (IP=80)
- **Proposta ordine**: 80 unità
- **Safety stock**: 12 unità (ridotto 20% per variabilità STABLE)

### 2. SKU_LOW (LOW - Basso Movimento)
- **Descrizione**: Prodotto Basso Movimento
- **Pattern vendite**: ~0.5 unità/giorno (vendite intermittenti: 1-0-1-0...)
- **Stock attuale**: 10 unità (IP=10)
- **Proposta ordine**: 5 unità (MOQ=5)
- **Safety stock**: 5 unità

### 3. SKU_SEASONAL (SEASONAL - Andamento Stagionale)
- **Descrizione**: Prodotto Andamento Stagionale
- **Pattern vendite**: Crescita da 5 a 17 unità/gg, poi decrescita (ciclo 21 giorni)
- **Stock attuale**: 46 unità (IP=46)
- **Proposta ordine**: 120 unità (arrotondato a pack_size=6)
- **Safety stock**: 20 unità

### 4. SKU_HIGH (HIGH - Alta Variabilità)
- **Descrizione**: Prodotto Alta Variabilità
- **Pattern vendite**: 8-30 unità/gg con fluttuazioni irregolari
- **Stock attuale**: 84 unità (IP=84)
- **Proposta ordine**: 210 unità (arrotondato a pack_size=10)
- **Safety stock**: 30 unità (aumentato 50% per variabilità HIGH)

## 📂 File Dataset

- **skus.csv**: 4 SKU con parametri completi (MOQ, pack_size, lead_time, safety_stock, etc.)
- **sales.csv**: 84 righe (21 giorni × 4 SKU)
- **transactions.csv**: 4 SNAPSHOT iniziali (18 Gen 2026)
- **order_logs.csv**: Vuoto (pronto per nuovi ordini)
- **receiving_logs.csv**: Vuoto (pronto per ricevimenti)

## 🔧 Parametri Chiave

Tutti gli SKU hanno:
- **Lead time**: 7 giorni
- **Review period**: 7 giorni
- **Forecast period**: 14 giorni (lead_time + review_period)
- **Shelf life**: 0 (nessun vincolo scadenza)
- **Max stock**: Ampio (100-800 unità)

## ✅ Validazione

Tutti gli SKU generano proposte d'ordine > 0:
- ✅ SKU_STABLE: 80 unità
- ✅ SKU_LOW: 5 unità
- ✅ SKU_SEASONAL: 120 unità
- ✅ SKU_HIGH: 210 unità

## 🎯 Utilizzo

Questo dataset è ideale per:
1. Testare la formula di riordino con diverse variabilità
2. Verificare l'impatto del safety stock
3. Dimostrare l'arrotondamento per pack_size e MOQ
4. Testare la GUI con dati realistici
5. Validare il calcolo della media giornaliera con pattern diversi

## 📝 Note

- Il dataset è **pulito** (nessun ordine o ricevimento pregresso)
- Gli SNAPSHOT iniziali sono calibrati per avere IP < target S
- Le vendite coprono esattamente 21 giorni (3 settimane complete)
- Ogni categoria di variabilità ha un pattern distintivo e riconoscibile
