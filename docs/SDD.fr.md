# Description de Conception Logicielle

## Pour OSRM API Gateway

**Version 0.3.0**  
Préparé par Luis Valverde  
lvalverdeb  
2026-06-25

## Table des Matières

- [1. Introduction](#1-introduction)
  - [1.1 Objectif du Document](#11-objectif-du-document)
  - [1.2 Périmètre du Sujet](#12-périmètre-du-sujet)
  - [1.3 Définitions, Acronymes et Abréviations](#13-définitions-acronymes-et-abréviations)
  - [1.4 Références](#14-références)
  - [1.5 Aperçu du Document](#15-aperçu-du-document)
- [2. Aperçu de la Conception](#2-aperçu-de-la-conception)
  - [2.1 Préoccupations des Parties Prenantes](#21-préoccupations-des-parties-prenantes)
  - [2.2 Points de Vue Sélectionnés](#22-points-de-vue-sélectionnés)
- [3. Vues de Conception](#3-vues-de-conception)
  - [3.1 Vue Contextuelle](#31-vue-contextuelle)
  - [3.2 Vue de Composition](#32-vue-de-composition)
  - [3.3 Vue Logique](#33-vue-logique)
  - [3.4 Vue d'Information](#34-vue-dinformation)
  - [3.5 Vue d'Interface](#35-vue-dinterface)
  - [3.6 Vue d'Interaction](#36-vue-dinteraction)
  - [3.7 Vue Algorithmique](#37-vue-algorithmique)
  - [3.8 Vue de Déploiement](#38-vue-de-déploiement)
  - [3.9 Vue de Concurrence](#39-vue-de-concurrence)
  - [3.10 Vue des Patrons](#310-vue-des-patrons)
- [4. Décisions](#4-décisions)
- [5. Annexes](#5-annexes)
  - [5.1 Formulation Mathématique du VRP](#51-formulation-mathématique-du-vrp)
  - [5.2 Configuration de la Limitation de Débit](#52-configuration-de-la-limitation-de-débit)

---

## 1. Introduction

### 1.1 Objectif du Document

Cette Description de Conception Logicielle (SDD) définit l'architecture et la conception du système OSRM API Gateway (v0.3.0). Elle sert de référence technique principale pour les développeurs, mainteneurs et opérateurs afin de comprendre comment le système est structuré, comment les composants interagissent et comment les décisions de conception correspondent aux exigences fonctionnelles. Le document décrit à la fois les étapes de conception préliminaire (architecturale) et détaillée (au niveau des composants).

**Public visé :** Ingénieurs logiciels, ingénieurs DevOps, architectes techniques, ingénieurs QA et futurs mainteneurs du système.

### 1.2 Périmètre du Sujet

OSRM API Gateway est un microservice asynchrone basé sur FastAPI qui encapsule le backend C++ d'OSRM (Open Source Routing Machine), exposant des capacités spécialisées de routage, d'appariement cartographique, d'optimisation et de résolution de Problèmes de Tournées de Véhicules (VRP) via une API RESTful JSON. Géographiquement focalisé sur le Costa Rica.

**Inclusions :**
- API HTTP RESTful avec 10 points d'accès
- Proxy HTTP asynchrone vers le backend OSRM avec pool de connexions
- Traitement de traces GPS par appariement cartographique
- Calcul de matrices distance/durée et conversion en graphes
- Optimisation du Problème du Voyageur de Commerce (TSP)
- Solveur VRP avec clustering Location-Allocation
- Proxy de tuiles vectorielles Mapbox (MVT)
- Limitation de débit sur tous les points d'accès
- Traçage distribué OpenTelemetry sur tous les chemins de requête
- Cache distribué Redis pour les réponses de routage/matrice

**Exclusions :**
- Internes du moteur C++ d'OSRM (traitement des données, algorithme de routage)
- Pipeline de traitement des données OSM (géré par deploy/docker/Dockerfile.builder)
- Visualisation côté client (exemples fournis mais hors périmètre)
- Authentification/autorisation

### 1.3 Définitions, Acronymes et Abréviations

| Terme | Définition |
|-------|------------|
| API | Interface de Programmation Applicative |
| MLD | Multi-Level Dijkstra - algorithme de routage d'OSRM |
| MVT | Mapbox Vector Tile - format binaire de tuile pour données cartographiques |
| OSRM | Open Source Routing Machine - moteur de routage en C++ |
| SDD | Document de Conception Logicielle |
| TSP | Problème du Voyageur de Commerce - optimisation d'itinéraire pour un véhicule |
| VRP | Problème de Tournées de Véhicules - optimisation d'itinéraires pour véhicules multiples |
| Pydantic | Bibliothèque de validation de données Python utilisant les annotations de type |
| FastAPI | Framework web asynchrone Python pour construire des API |
| httpx | Client HTTP asynchrone Python |
| NetworkX | Bibliothèque d'analyse de graphes Python |
| Hystérésis | Distance tampon empêchant le basculement d'affectation près des limites de dépôt |
| Location-Allocation | Algorithme de clustering attribuant les arrêts aux dépôts optimaux |
| Euclidienne | Distance en ligne droite entre deux points |
| OTel | OpenTelemetry - framework d'observabilité pour le traçage distribué |
| Redis | Serveur de dictionnaire distant - magasin de structures de données en mémoire utilisé comme cache |
| W3C TraceContext | Standard pour propager le contexte de trace entre les limites de service (en-tête traceparent) |
| Cache-Aside | Modèle de cache applicatif : lire du cache, en cas de miss charger la source et peupler le cache |

### 1.4 Références

| Référence | Version | Type |
|-----------|---------|------|
| Documentation de l'API OSRM v1 | v1.0 | Normative |
| Documentation FastAPI | 0.136.x | Normative |
| Documentation Pydantic v2 | 2.x | Normative |
| IEEE Std 1016-2009 (SDD) | 2009 | Informative |
| GitHub Spec Kit (Méthodologie SDD) | dernière | Informative |
| GATEWAY_IMPLEMENTATION_PLAN.md | v0.2.2 | Normative |
| API_REFERENCE.md | v0.2.2 | Normative |
| docs/planning/vrp_proposal.md | v0.2.2 | Informative |

### 1.5 Aperçu du Document

La Section 2 présente l'aperçu de la conception, les préoccupations des parties prenantes et les points de vue sélectionnés. La Section 3 contient les vues de conception concrètes (Contexte, Composition, Logique, Information, Interface, Interaction, Algorithme, Déploiement, Concurrence, Patrons). La Section 4 consigne les décisions architecturales significatives. La Section 5 contient des annexes avec du matériel supplémentaire.

---

## 2. Aperçu de la Conception

### 2.1 Préoccupations des Parties Prenantes

| Partie Prenante | Préoccupations | Adressé Par |
|-----------------|----------------|-------------|
| Développeurs d'Applications | Responsabilités des composants, API, modèles de données | Vues Logique, Interface, Information |
| DevOps/SRE | Topologie de déploiement, passage à l'échelle, health checks | Vues Déploiement, Concurrence |
| Ingénieurs QA | Testabilité, gestion d'erreurs, limites de débit | Vues Interaction, Interface |
| Chefs de Produit | Périmètre fonctionnel, capacités VRP, couverture API | Vues Contexte, Composition |
| Futurs Mainteneurs | Justification de la conception, décisions algorithmiques | Sections Algorithme, Patrons, Décisions |

### 2.2 Points de Vue Sélectionnés

| Point de Vue | Préoccupations Adressées |
|--------------|--------------------------|
| Contexte | Limites du système, acteurs externes (Client, Backend OSRM) |
| Composition | Décomposition en modules (services, modèles, couche API) |
| Logique | Hiérarchie de classes, système de types, héritage des modèles Pydantic |
| Information | Schémas de données, modèles Pydantic, contrats requête/réponse |
| Interface | Spécification de l'API REST, contrat HTTP OSRM |
| Interaction | Flux de données VRP, motifs de requête asynchrone, propagation d'erreurs |
| Algorithme | Location-Allocation VRP, segmentation TSP, logique d'hystérésis |
| Déploiement | Topologie Docker Compose, builds multi-étapes, réseautique |
| Concurrence | Modèle d'E/S asynchrone, pool de connexions, limites de débit |
| Patrons | Patron Gateway, Patron Service, Injection de Dépendances |

---

## 3. Vues de Conception

### 3.1 Vue Contextuelle

**ID:** CTX-001  
**Titre:** Contexte du Système  
**Point de Vue:** Contexte  
**Représentation :**

```
┌─────────────┐     HTTP/JSON      ┌───────────────────────────────────┐
│   Client    │ ──────────────────> │  OSRM API Gateway (Port 8000)     │
│ (App/Navig.)│                    │  FastAPI + Uvicorn ASGI Server    │
│             │ <────────────────── │                                   │
└─────────────┘     HTTP/JSON      │  Points d'accès: /route, /matrix, │
                                     │  /match, /trip, /nearest, /tile, │
                                     │  /vrp, /vrp/allocate, /health,   │
                                     │  /matrix-graph                   │
                                     └───────────┬───────────────────────┘
                                                 │ HTTP (httpx AsyncClient)
                                                 │ Port 5000
                                                 ▼
                                     ┌───────────────────────────────────┐
                                     │   Backend OSRM (Port 5000)        │
                                     │  Moteur C++ (Algorithme MLD)      │
                                     │  Profils : car, bicycle, foot     │
                                     │  Données : costa-rica-latest.osrm │
                                     └───────────────────────────────────┘
```

Le système fonctionne comme une passerelle entre les clients et le backend C++ d'OSRM. Les clients interagissent exclusivement avec la passerelle FastAPI Python, qui traduit les requêtes JSON de haut niveau en paramètres de requête HTTP OSRM et enrichit les réponses avec des calculs supplémentaires (construction de graphes, résolution VRP).

### 3.2 Vue de Composition

**ID:** CMP-001  
**Titre:** Décomposition en Modules  
**Point de Vue:** Composition  
**Représentation :**

```
┌───────────────────────────────────────────────────────────┐
│                  OSRM API Gateway                          │
├───────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────┐  │
│  │  Couche API (app/main.py)                            │  │
│  │  Application FastAPI, 10 endpoints, gestion erreurs, │  │
│  │  limiteurs de débit (slowapi), validation requêtes   │  │
│  └──────────┬──────────────────────────────────────────┘  │
│             │                                             │
│    ┌────────┴─────────┬──────────────────┐               │
│    ▼                  ▼                  ▼                │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────────────┐│
│  │ OSRM     │  │ GraphBuilder │  │ VrpService            ││
│  │ Client   │  │ (graph_      │  │ (vrp_service.py)      ││
│  │ (osrm_   │  │  builder.py) │  │ Location-Allocation + ││
│  │ client   │  │ Graphe Di-   │  │ Optimisation TSP      ││
│  │ .py)     │  │ rect Net-    │  │ Dépend de :           ││
│  │ Proxy    │  │ workX depuis │  │ OSRMClient            ││
│  │ HTTP     │  │ matrice      │  │                       ││
│  │ Async.   │  │              │  │                       ││
│  └──────────┘  └──────────────┘  └──────────────────────┘│
│                                                           │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  Couche Modèles (app/models/schemas.py)              │  │
│  │  15 modèles Pydantic v2 : Coordinate, Stop,         │  │
│  │  RouteReq, MatchReq, MatrixReq, TripReq,            │  │
│  │  NearestReq, VrpReq, VrpResponse, VehicleRoute, etc.│  │
│  └─────────────────────────────────────────────────────┘  │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐  │
│  │  Couche Configuration (app/config.py)                │  │
│  │  Paramètres Pydantic : OSRM_BASE_URL, limites débit,│  │
│  │  APP_NAME, chargés depuis .env                       │  │
│  └─────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────┘
```

### 3.3 Vue Logique

**ID:** LOG-001  
**Titre:** Hiérarchie des Types Centraux  
**Point de Vue:** Logique  
**Représentation :**

```
┌─────────────────────────────┐
│  BaseModel (Pydantic)       │
└──────────┬──────────────────┘
           │
     ┌──────┴──────┐
     ▼             ▼
┌──────────┐ ┌─────────────┐
│Coordinate│ │CommonRouting│
│          │ │Options      │
│longitude │ │bearings     │
│latitude  │ │radiuses     │
└────┬─────┘ │hints        │
     │       │approaches   │
     ▼       │exclude      │
┌──────────┐ │snapping     │
│Stop      │ │skip_waypoints│
│id: str/int│└──────┬──────┘
└──────────┘        │
           ┌────────┼────────┬───────────┐
           ▼        ▼        ▼           ▼
     ┌────────┐┌────────┐┌────────┐ ┌─────────┐
     │RouteReq││MatchReq││Matrix  │ │TripReq  │
     │origin  ││bread-  ││Req     │ │coords   │
     │dest    ││crumbs  ││coords  │ │roundtrip│
     │waypoints││profile ││annot.  │ │source   │
     │steps   ││gaps    ││fallback│ │dest     │
     └────────┘│tidy    │└────────┘ └─────────┘
               └────────┘
                              ┌──────────────┐
                         ┌───>│VrpResponse   │
                         │    │code: str     │
                         │    │routes: List  │
                         │    │total_distance│
                         │    └──────────────┘
┌────────────┐            │
│VrpRequest  │────────────┤
│depots      │            │    ┌──────────────────┐
│stops       │────────────┼───>│VrpAllocationResp  │
│vehicle_ct  │            │    │code: str          │
│capacity    │            │    │allocations: Dict  │
│max_radius  │            │    │unreachable_stops  │
│clustering  │            │    └──────────────────┘
│hysteresis  │            │
│roundtrip   │            │    ┌──────────────┐
└────────────┘            └───>│VehicleRoute  │
                               │vehicle_id    │
                               │depot_index   │
                               │stops_indices │
                               │route_geometry│
                               │distance_meters│
                               │duration_secs │
                               └──────────────┘
```

**ID:** LOG-002  
**Titre:** Classes de la Couche Service  
**Point de Vue:** Logique  
**Représentation :**

```
┌──────────────────────────┐
│ OSRMClient               │
├──────────────────────────┤
│ - base_url: str          │
│ - _client: AsyncClient   │
├──────────────────────────┤
│ + close()                │
│ + get_route(coords, req) │
│ + get_matrix(req)        │
│ + match_trace(req)       │
│ + get_trip(req)          │
│ + get_nearest(req)       │
│ + get_tile(profile,z,x,y)│
│ - _get(endpoint, params) │
│ + _serialize_common_opts │
└──────────────────────────┘

┌────────────────────────────────┐
│ GraphBuilder                   │
├────────────────────────────────┤
│ + build_from_matrix(data, req) │
│   → node_link_data (JSON)      │
└────────────────────────────────┘

┌──────────────────────────────────────────────────┐
│ VrpService                                       │
├──────────────────────────────────────────────────┤
│ - osrm_client: OSRMClient                        │
├──────────────────────────────────────────────────┤
│ + solve_vrp(req) → VrpResponse                   │
│ + allocate_products(req) → VrpAllocationResponse │
│ - _get_allocation_data(req)                      │
│ - _solve_tsp_chunk(...) → VehicleRoute           │
│ - _get_depot_to_stop_matrix(depots, stops)       │
│ - _allocate_stops(durations, distances, ...)     │
└──────────────────────────────────────────────────┘

┌────────────────────────┐
│ Settings               │
├────────────────────────┤
│ OSRM_BASE_URL: str     │
│ APP_NAME: str          │
│ DEBUG: bool            │
│ RATE_LIMIT_*: str      │
└────────────────────────┘
```

### 3.4 Vue d'Information

**ID:** INF-001  
**Titre:** Dictionnaire de Données Requête/Réponse  
**Point de Vue:** Information  
**Représentation :**

| Modèle | Champ | Type | Contraintes | Description |
|--------|-------|------|-------------|-------------|
| Coordinate | longitude | float | [-180, 180] | Longitude WGS84 |
| Coordinate | latitude | float | [-90, 90] | Latitude WGS84 |
| Stop | id | str\|int\|null | optionnel | Identifiant unique d'arrêt |
| RouteRequest | origin | Coordinate | requis | Point de départ |
| RouteRequest | destination | Coordinate | requis | Point d'arrivée |
| RouteRequest | waypoints | List[Coord] | max 200 | Arrêts intermédiaires |
| RouteRequest | profile | enum | driving/cycling/walking | Profil de routage |
| RouteRequest | steps | bool | default true | Instructions pas à pas |
| RouteRequest | alternatives | bool\|int | default false | Itinéraires alternatifs |
| MatchRequest | breadcrumbs | List[GPSBreadcrumb] | [2, 5000] | Points de trace GPS |
| MatrixRequest | coordinates | List[Coordinate] | [2, 5000], sources x destinations <= MATRIX_MAX_CELLS | Points pour la matrice |
| MatrixRequest | annotations | enum | duration/distance/les deux | Métriques de coût |
| TripRequest | coordinates | List[Coordinate] | [2, 200] | Points à optimiser |
| VrpRequest | depots | List[Stop] | [1, 500] | Emplacements des entrepôts |
| VrpRequest | stops | List[Stop] | [1, VRP_MAX_STOPS] | Points de livraison |
| VrpRequest | capacity | int | [1, 10000], default 35 | Capacité par véhicule |
| VrpRequest | clustering_mode | enum | distance/travel_time/radial | Stratégie d'allocation |
| VehicleRoute | route_geometry | Dict | GeoJSON | Itinéraire optimisé |
| VehicleRoute | distance_meters | float | >= 0 | Distance totale de l'itinéraire |
| VehicleRoute | duration_seconds | float | >= 0 | Durée totale de l'itinéraire |

### 3.5 Vue d'Interface

**ID:** INT-001  
**Titre:** Surface de l'API REST  
**Point de Vue:** Interface  
**Représentation :**

| Méthode | Chemin | Corps Requête | Réponse | Limite de Débit |
|---------|--------|---------------|---------|-----------------|
| GET | /health | — | `{status, service}` | — |
| POST | /route | RouteRequest | OSRM Route JSON | 600/min |
| POST | /matrix | MatrixRequest | OSRM Table JSON | 300/min |
| POST | /matrix-graph | MatrixRequest | `{nodes, edges}` | 300/min |
| POST | /match | MatchRequest | OSRM Match JSON | 600/min |
| POST | /trip | TripRequest | OSRM Trip JSON | 300/min |
| POST | /nearest | NearestRequest | OSRM Nearest JSON | 600/min |
| GET | /tile/{p}/{z}/{x}/{y}.mvt | — | application/x-protobuf | 600/min |
| POST | /vrp | VrpRequest | VrpResponse | 100/min |
| POST | /vrp/allocate | VrpRequest | VrpAllocationResponse | 100/min |

**Enveloppe d'erreur commune :**
```
HTTP 400/422/500
{"detail": {"code": "InvalidValue", "message": "..."}}
```

**Interface amont OSRM** (interne, consommée par OSRMClient) :
```
GET /{service}/v1/{profile}/{coordinates}?{params}
Services : route, table, match, trip, nearest, tile
Profils : driving, cycling, walking
```

### 3.6 Vue d'Interaction

**ID:** INT-ACT-001  
**Titre:** Flux de Résolution VRP  
**Point de Vue:** Interaction  
**Représentation :**

```
Client          API Gateway          OSRMClient        Backend OSRM
  │                  │                    │                   │
  │ POST /vrp        │                    │                   │
  │─────────────────>│                    │                   │
  │                  │                    │                   │
  │                  │ solve_vrp(req)     │                   │
  │                  │────────────────────│                   │
  │                  │                    │                   │
  │                  │ _get_allocation    │                   │
  │                  │   _data(req)       │                   │
  │                  │────────────────────│                   │
  │                  │                    │                   │
  │                  │ _get_depot_to_stop │                   │
  │                  │   _matrix(d,s)     │                   │
  │                  │────────────────────│                   │
  │                  │                    │ GET /table/v1/... │
  │                  │                    │ [lots de 500]    │
  │                  │                    │──────────────────>│
  │                  │                    │                   │
  │                  │                    │ JSON (durations   │
  │                  │                    │  + distances)     │
  │                  │                    │<──────────────────│
  │                  │                    │                   │
  │                  │ return matrix      │                   │
  │                  │<───────────────────│                   │
  │                  │                    │                   │
  │                  │ _allocate_stops(   │                   │
  │                  │   durations,       │                   │
  │                  │   distances, ...)  │                   │
  │                  │  ┌─────────────────┐                   │
  │                  │  │ Pour chaque     │                   │
  │                  │  │ arrêt :         │                   │
  │                  │  │ 1. Trouver      │                   │
  │                  │  │    meilleur     │                   │
  │                  │  │    dépôt par    │                   │
  │                  │  │    métrique     │                   │
  │                  │  │ 2. Appliquer    │                   │
  │                  │  │    hystérésis   │                   │
  │                  │  │ 3. Vérifier     │                   │
  │                  │  │    cohérence    │                   │
  │                  │  │ 4. Appliquer    │                   │
  │                  │  │    rayon max    │                   │
  │                  │  └─────────────────┘                   │
  │                  │                    │                   │
  │                  │ pour chaque        │                   │
  │                  │ cluster :          │                   │
  │                  │ _solve_tsp_chunk   │                   │
  │                  │────────────────────│                   │
  │                  │                    │ GET /trip/v1/...  │
  │                  │                    │──────────────────>│
  │                  │                    │                   │
  │                  │                    │ JSON (optimisé)   │
  │                  │                    │<──────────────────│
  │                  │                    │                   │
  │                  │ return VehicleRoute│                   │
  │                  │<───────────────────│                   │
  │                  │                    │                   │
  │                  │ VrpResponse        │                   │
  │                  │────────────────────│                   │
  │                  │                    │                   │
  │ 200 JSON         │                    │                   │
  │<─────────────────│                    │                   │
```

**ID:** INT-ACT-002  
**Titre:** Flux Simple de Requête d'Itinéraire  
**Point de Vue:** Interaction  
**Représentation :**

```
Client          API Gateway          OSRMClient        Backend OSRM
  │                  │                    │                   │
  │ POST /route      │                    │                   │
  │─────────────────>│                    │                   │
  │                  │ origin+dest+       │                   │
  │                  │ waypoints→coords   │                   │
  │                  │                    │                   │
  │                  │ get_route(coords,  │                   │
  │                  │   request)         │                   │
  │                  │────────────────────│                   │
  │                  │                    │ GET /route/v1/... │
  │                  │                    │──────────────────>│
  │                  │                    │                   │
  │                  │                    │ JSON response     │
  │                  │                    │<──────────────────│
  │                  │                    │                   │
  │                  │ return raw JSON    │                   │
  │                  │<───────────────────│                   │
  │                  │                    │                   │
  │ 200 JSON         │                    │                   │
  │<─────────────────│                    │                   │
```

### 3.7 Vue Algorithmique

**ID:** ALG-001  
**Titre:** Location-Allocation VRP avec Hystérésis  
**Point de Vue:** Algorithme  
**Représentation :**

**Objectif :** Attribuer chaque arrêt de livraison au dépôt (entrepôt) optimal en tenant compte des coûts du réseau routier et de la stabilité.

**Entrées :**
- `durations` : matrice `[num_depots × num_stops]` des temps de trajet (secondes)
- `distances` : matrice `[num_depots × num_stops]` des distances routières (mètres)
- `depots` : liste des coordonnées des dépôts
- `stops` : liste des coordonnées des arrêts
- `max_radius_m` : limite de distance optionnelle
- `mode` : `"travel_time"` ou `"distance"` ou `"radial"`
- `hysteresis_m` : tampon empêchant le basculement (défaut 2000m)

**Pseudocode :**

```
1. SÉLECTIONNER target_matrix = durations si mode=="travel_time" sinon distances
2. CALCULER distances euclidiennes de chaque arrêt à chaque dépôt
   (en utilisant DEG_TO_M ≈ 110600m/degré, cos(lat) ≈ 0.98 pour longitude)
3. POUR chaque arrêt s :
   a. anchor_depot = argmin(distance euclidienne à s)
   b. SI mode == "radial" :
        ATTRIBUER s à anchor_depot ; CONTINUER
   c. best_depot = argmin(target_matrix[:, s])
   d. SI euclidienne(best_depot) - euclidienne(anchor) > 50km :
        UTILISER anchor_depot (surcharge de cohérence visuelle)
   e. SI best_val ou anchor_val ≈ infini :
        UTILISER le joignable
   f. SINON appliquer hystérésis :
        SI target[best] < target[anchor] - hysteresis :
          ATTRIBUER à best_depot
        SINON :
          ATTRIBUER à anchor_depot
   g. SI max_radius et distance > max_radius :
        MARQUER comme inaccessible
      SINON :
        AJOUTER à l'affectation
4. RETOURNER {allocations, unreachable_stops}
```

**Conversion d'hystérésis (mode temps) :** `effective_hysteresis = hysteresis_m / 11.1` secondes (≈ 2km à 40km/h).

**TSP par lots (Phase 2) :**

```
Pour chaque paire (dépôt, cluster) :
  1. SUBDIVISER le cluster en lots de min(80, capacity)
  2. POUR chaque lot :
     a. CONSTRUIRE TripRequest(depot + chunk, source="first",
                              destination="any", roundtrip=request.roundtrip)
     b. APPELER OSRM /trip/v1/{profile}/{coords}
     c. RÉORDONNER les arrêts selon waypoint.trips_index et waypoint_index
     d. APPLIQUER une correspondance des waypoints triés aux indices d'arrêts originaux
     e. RETOURNER VehicleRoute avec geometry, distance, duration
  3. AGGRÉGER les itinéraires dans VrpResponse
```

### 3.8 Vue de Déploiement

**ID:** DEP-001  
**Titre:** Topologie Docker Compose  
**Point de Vue:** Déploiement  
**Représentation :**

```
┌─────────────────────────────────────────────────────────────┐
│                   Hôte Docker                               │
│                                                              │
│  ┌─ osrm-data-builder ─────────────────────────────────┐     │
│  │  Image : osrm-data-builder:latest                    │     │
│  │  Profil : build (exécution manuelle)                 │     │
│  │  CMD : osrm-extract → osrm-partition → osrm-customize│     │
│  │  Données : costa-rica-latest.osm.pbf → fichiers.osrm │     │
│  └──────────────────────────────────────────────────────┘     │
│                                                              │
│  ┌─ osrm ───────────────────────────────────────────────┐     │
│  │  Image : osrm-backend:latest (Dockerfile multi-étape) │     │
│  │  Conteneur : osrm-backend                             │     │
│  │  Port : 5000 → 5000                                   │     │
│  │  CMD : osrm-routed --algorithm mld --max-trip-size 200│     │
│  │  Profils : car (défaut), bicycle, foot                │     │
│  │  Volume : /data/car/ ← fichiers .osrm traités        │     │
│  │  Plateforme : linux/amd64                             │     │
│  └──────────────────────────────────────────────────────┘     │
│                                                              │
│  ┌─ api ────────────────────────────────────────────────┐     │
│  │  Image : osrm-api-gateway (Dockerfile)                │     │
│  │  Conteneur : osrm-api-gateway                         │     │
│  │  Port : 8080 → 8000 (FastAPI interne)                 │     │
│  │  ENV : OSRM_BASE_URL=http://osrm-backend:5000         │     │
│  │  CMD : uvicorn app.main:app --host 0.0.0.0 --port 8000│     │
│  │  Dépend de : osrm (health check)                      │     │
│  │  Redémarrage : always                                 │     │
│  └──────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

**Flux de données au déploiement :** Le conteneur `osrm-data-builder` s'exécute une fois (profil build) pour traiter les données OSM PBF brutes en fichiers de graphe de routage. Le conteneur `osrm` charge ces fichiers et sert l'API de routage. Le conteneur `api` se connecte à `osrm` via le réseau interne Docker.

### 3.9 Vue de Concurrence

**ID:** CONC-001  
**Titre:** E/S Asynchrones et Limitation de Débit  
**Point de Vue:** Concurrence  
**Représentation :**

Le système utilise un modèle asynchrone monothread :

1. **Serveur ASGI :** Uvicorn exécute l'application FastAPI avec des workers asynchrones. Tous les gestionnaires de points d'accès sont `async def`.
2. **Pool de Clients HTTP :** `OSRMClient` initialise un seul `httpx.AsyncClient` avec pool de connexions, réutilisé pour toutes les requêtes. Timeout par défaut : 30s.
3. **Limitation de Débit :** Middleware `slowapi` avec limites par point d'accès appliquées via le décorateur `@limiter.limit(...)`. Fonction clé : `get_remote_address` (le pair immédiat ; les déploiements passent `--forwarded-allow-ips` afin que le `X-Forwarded-For` d'un proxy de confiance désigne le vrai client). Configuration :
   - `/route` : 600 req/min
   - `/matrix`, `/matrix-graph` : 300 req/min
   - `/match` : 600 req/min
   - `/trip` : 300 req/min
   - `/vrp`, `/vrp/allocate` : 100 req/min
   - `/nearest`, `/tile` : 600 req/min
4. **Pas de threading/parallélisme :** Le service VRP traite les allocations et les lots TSP séquentiellement dans une seule requête. Le traitement par lots des matrices (500 arrêts/lot) utilise des appels `await` séquentiels.

### 3.10 Vue des Patrons

**ID:** PAT-001  
**Titre:** Patrons Architecturaux Utilisés  
**Point de Vue:** Patrons  
**Représentation :**

| Patron | Application | Emplacement |
|--------|-------------|-------------|
| **Passerelle (Gateway)** | Point d'entrée unique traduisant entre client et backend | `app/main.py` (tous les endpoints) |
| **Proxy** | Transit vers OSRM avec requête/réponse enrichie | Méthodes de `OSRMClient` |
| **Couche de Service** | Logique métier encapsulée dans des classes de service | `app/services/*` |
| **Injection de Dépendances** | `VrpService` reçoit `OSRMClient` via le constructeur | `VrpService.__init__(osrm_client)` |
| **Constructeur (Builder)** | `GraphBuilder` construit un objet complexe (graphe NetworkX) à partir de données matricielles | `GraphBuilder.build_from_matrix()` |
| **Paramètres (Settings)** | Configuration basée sur l'environnement via Pydantic BaseSettings | `app/config.py:Settings` |
| **Stratégie** | `clustering_mode` sélectionne l'algorithme d'allocation (distance/temps/radial) | Paramètre mode de `_allocate_stops()` |
| **Traitement par Lots** | Matrices volumineuses divisées en lots de 500 arrêts | `_get_depot_to_stop_matrix()` |
| **Cache-Aside** | L'application lit L1 (en mémoire), en miss lit L2 (Redis), en miss interroge OSRM et peuple les deux niveaux | `OSRMClient._get()` + `RedisCache` |
| **Traçage Distribué OpenTelemetry** | Middleware de traçage auto-instrumentant FastAPI et httpx, exportant les spans via OTLP | `app/tracing.py` |

---

## 4. Décisions

**ID:** DEC-001  
**Titre:** Architecture Passerelle plutôt qu'Exposition Directe d'OSRM  
**Contexte :** Les clients pourraient appeler OSRM directement, mais cela exposerait des paramètres de requête HTTP bruts, manquerait de limitation de débit et forcerait chaque client à implémenter le codage des paramètres délimités par des points-virgules d'OSRM.  
**Options :** (a) Proxy OSRM direct, (b) Passerelle complète avec validation et enrichissement, (c) Couche GraphQL.  
**Résultat :** Option choisie (b) — Passerelle FastAPI complète avec validation Pydantic, limitation de débit et corps de requête/réponse JSON riches. Fournit une surface d'API stable et documentée.  
**Plus d'Informations :** Le patron Passerelle simplifie l'intégration client et centralise les préoccupations transversales.

**ID:** DEC-002  
**Titre:** Asynchrone sur l'Ensemble du Système  
**Contexte :** Le système effectue de nombreux appels HTTP sortants qui bloquent sur les E/S. Utiliser du code synchrone limiterait le débit sous des requêtes concurrentes.  
**Options :** (a) Synchrone avec pool de threads, (b) Entièrement asynchrone avec FastAPI + httpx, (c) ASGI avec endpoints synchrones.  
**Résultat :** Option choisie (b) — Tous les endpoints et méthodes de service sont asynchrones, utilisant un seul `httpx.AsyncClient` avec pool de connexions. Maximise le débit sous charge concurrente.

**ID:** DEC-003  
**Titre:** Architecture VRP : Allocation en Deux Phases + TSP  
**Contexte :** OSRM ne supporte pas nativement le routage multi-véhicules. Un solveur VRP personnalisé était nécessaire, déléguant le routage au service `/trip` d'OSRM.  
**Options :** (a) Appels OSRM trip purs par véhicule (sans allocation), (b) Location-Allocation + TSP, (c) Solveur VRP externe (OR-Tools, jsprit).  
**Résultat :** Option choisie (b) — Location-Allocation avec clustering par hystérésis, puis TSP par cluster via OSRM `/trip`. Équilibre la qualité algorithmique avec la réutilisation d'OSRM.  
**Plus d'Informations :** voir `docs/planning/vrp_proposal.md`.

**ID:** DEC-004  
**Titre:** Stabilité d'Affectation Basée sur l'Hystérésis  
**Contexte :** Les arrêts près des limites des dépôts pourraient changer d'affectation entre les requêtes en raison de petites variations de mesure, provoquant des itinéraires incohérents.  
**Options :** (a) Toujours choisir le dépôt le plus proche, (b) Tampon d'hystérésis, (c) Départage aléatoire.  
**Résultat :** Option choisie (b) — Un tampon de 2000m (configurable) empêche le basculement : un arrêt reste avec son dépôt actuel à moins qu'un dépôt différent ne soit `hysteresis_m` meilleur. Inclut également une vérification de cohérence euclidienne de 50km.

**ID:** DEC-005  
**Titre:** Modèles Pydantic v2 plutôt que Dictionnaires Bruts  
**Contexte :** Toutes les requêtes API nécessitent validation, sérialisation et documentation.  
**Options :** (a) Analyse de dictionnaires bruts, (b) Pydantic v1, (c) Pydantic v2, (d) Marshmallow.  
**Résultat :** Option choisie (c) — Pydantic v2 avec `BaseModel` et contraintes `Field` fournit une validation automatique, une génération de schémas JSON pour la documentation OpenAPI et une sérialisation rapide.

**ID:** DEC-006  
**Titre:** Builds Docker Multi-Étapes pour le Traitement des Données OSRM  
**Contexte :** L'extraction des données OSRM est lente (~30min) et nécessite des outils différents du serveur d'exécution.  
**Options :** (a) Build mono-étape avec tous les outils, (b) Multi-étapes avec constructeur et exécution séparés, (c) Volume de données pré-traitées.  
**Résultat :** Option choisie (b) — Trois Dockerfiles dans `deploy/docker/` : `Dockerfile.builder` (extraction/partitionnement/personnalisation), `Dockerfile.osrm` (exécution) et `Dockerfile` (passerelle API). Seules les images d'exécution sont utilisées en production.

**ID:** DEC-007  
**Titre:** slowapi pour la Limitation de Débit  
**Contexte :** Les points d'accès nécessitent une protection contre une utilisation excessive.  
**Options :** (a) Middleware personnalisé, (b) slowapi, (c) Limitation de débit au niveau Nginx.  
**Résultat :** Option choisie (b) — `slowapi` avec suivi de débit en mémoire. Configuration simple par point d'accès via des décorateurs. Nginx pourrait être ajouté en amont pour les déploiements distribués.

**ID :** DEC-008  
**Titre :** Nouvelles Essais avec Backoff Exponentiel pour les Défaillances Transitoires d'OSRM  
**Contexte :** OSRM peut retourner des 5xx sporadiques ou des timeouts sous charge. Une seule défaillance propagerait une erreur 500 au client.  
**Options :** (a) Laisser propager les défaillances, (b) Nouvelles essais avec délai fixe, (c) Backoff exponentiel avec jitter.  
**Résultat :** Option choisie (c) — `tenacity` avec backoff exponentiel (1s → 10s max, 3 tentatives). Ne réessaie que sur 5xx, timeouts et erreurs de transport. Les erreurs 4xx ne sont jamais réessayées.

**ID :** DEC-009  
**Titre :** Cache des Réponses pour les Requêtes OSRM Répétées  
**Contexte :** La même requête de routage ou de matrice peut être soumise plusieurs fois en quelques minutes. Les appels inutiles à OSRM gaspillent des ressources.  
**Options :** (a) Pas de cache, (b) Cache en mémoire avec TTL, (c) Cache Redis.  
**Résultat :** Option choisie (b) — `cachetools.TTLCache` avec TTL de 15 minutes et limite de 1024 entrées. Stratégie cache-first : `_get` retourne les données cachées immédiatement, passant à OSRM en cas de miss.

**ID :** DEC-010  
**Titre :** Métriques Prometheus pour l'Observabilité  
**Contexte :** Le système n'avait aucune visibilité sur la latence, le taux d'erreur ou le débit.  
**Options :** (a) Pas de métriques, (b) Prometheus client avec instrumentation personnalisée, (c) Exportateur OpenTelemetry.  
**Résultat :** Option choisie (b) — `prometheus-fastapi-instrumentator` auto-instrumente tous les points d'accès. Expose `/metrics` au format Prometheus.

**ID :** DEC-011  
**Titre :** FastAPI Lifespan pour la Fermeture Gracieuse du Pool de Connexions  
**Contexte :** Le pool `httpx.AsyncClient` n'était jamais explicitement fermé, causant des avertissements de transport non fermé.  
**Résultat :** `OSRMClient.close()` connecté au context manager `lifespan` de FastAPI. Le pool est démantelé gracieusement lors de l'arrêt du serveur ASGI.

**ID :** DEC-012  
**Titre :** Traçage Distribué OpenTelemetry  
**Contexte :** Le système s'étend sur deux services et effectue plusieurs appels HTTP sortants par requête. Une visibilité de la répartition de la latence et de la corrélation de bout en bout est nécessaire.  
**Options :** (a) Pas de traçage, (b) IDs de corrélation personnalisés, (c) OpenTelemetry avec W3C TraceContext.  
**Résultat :** Option choisie (c) — SDK OpenTelemetry avec `opentelemetry-instrumentation-fastapi` et `opentelemetry-instrumentation-httpx`. Spans exportés via OTLP. En-têtes W3C `traceparent` propagés au backend OSRM.

**ID :** DEC-013  
**Titre :** Cache Distribué Redis  
**Contexte :** Le TTLCache en mémoire (DEC-009) est perdu au redémarrage, non partagé entre réplicas, et limité à 1024 entrées.  
**Options :** (a) Uniquement en mémoire, (b) Redis comme L2 derrière L1 en mémoire, (c) Uniquement Redis.  
**Résultat :** Option choisie (b) — Deux niveaux : L1 est `cachetools.TTLCache` (lectures locales sub-milliseconde), L2 est Redis (partagé entre instances, survit aux redémarrages). Modèle Cache-Aside : `_get()` consulte L1 → L2 → OSRM.

---

## 5. Annexes

### 5.1 Formulation Mathématique du VRP

**Phase de Location-Allocation :**

Étant donnés des dépôts `D = {d₁, ..., dₘ}` et des arrêts `S = {s₁, ..., sₙ}`, avec une matrice de coûts `C ∈ ℝ^{m×n}` où `C[i][j]` est le temps de trajet (ou la distance) du dépôt `dᵢ` à l'arrêt `sⱼ` :

Attribuer chaque arrêt `sⱼ` à exactement un dépôt `dᵢ` tel que :

```
affectation(sⱼ) = dᵢ  où  i = argmin_k C[k][j]
```

Sous réserve de :
- **Hystérésis :** `C[best][j] < C[anchor][j] - h` pour réaffectation
- **Cohérence :** `euclidienne(best) - euclidienne(anchor) < 50km`
- **Rayon maximal :** `distance_routière(i, j) ≤ max_radius_km`
- **Mode radial :** Utilise la distance euclidienne au lieu du coût routier

**Phase TSP (par cluster) :**

Pour chaque dépôt `d` avec les arrêts attribués `S' = {s'₁, ..., s'ₖ}` :

Trouver la permutation `π` minimisant le coût total aller-retour :

```
minimiser     distance(d, s'_{π₁}) + Σ_{t=1}^{k-1} distance(s'_{πₜ}, s'_{π_{t+1}}) + distance(s'_{πₖ}, d)
sous contrainte 1 ≤ πₜ ≤ k,  ∀t
                πₜ ≠ πₛ  pour t ≠ s
```

Délégué au service OSRM `/trip` qui utilise des heuristiques spécialisées sur la hiérarchie de contraction.

### 5.2 Configuration de la Limitation de Débit

| Point d'accès | Limite | Fenêtre |
|---------------|--------|---------|
| /route | 600 | 1 minute |
| /matrix | 300 | 1 minute |
| /matrix-graph | 300 | 1 minute |
| /match | 600 | 1 minute |
| /trip | 300 | 1 minute |
| /nearest | 600 | 1 minute |
| /tile | 600 | 1 minute |
| /vrp | 100 | 1 minute |
| /vrp/allocate | 100 | 1 minute |

Configuré via `app/config.py:Settings` avec remplacement par variables d'environnement.
