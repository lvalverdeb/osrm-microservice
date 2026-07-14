# Microservice Backend OSRM

[English](https://github.com/lvalverde/osrm-microservice/blob/main/README.md) | [Español](https://github.com/lvalverde/osrm-microservice/blob/main/README.es.md) | [Français](https://github.com/lvalverde/osrm-microservice/blob/main/README.fr.md)

Routage haute performance et appariement de cartes (map-matching) pour le Costa Rica.

## Instructions de Configuration

Ce projet utilise un flux de travail de **Construction Locale et Transfert Groupé** pour prendre en charge le déploiement sur des hôtes Docker distants tout en traitant les données localement sur macOS.

### 1. Prérequis

- Docker Desktop (macOS)
- Hôte Docker Distant (ex: VM Linux à `10.211.55.28`)
- `make`

### 2. Acquisition de Données et Traitement Local

Extrayez et traitez les données OSM du Costa Rica localement. Ce processus regroupe les données dans votre dossier local `./data` en utilisant un constructeur basé sur Docker "Sans Montage".

```bash
# Télécharger les dernières données cartographiques du Costa Rica
make download-data

# Traiter les données localement pour un profil spécifique (car, bicycle, foot)
# Par défaut sur car si PROFILE est omis
make process-osrm PROFILE=car
```

### 3. Déploiement Distant

Déployez l'API et le moteur OSRM sur l'hôte distant. Les données traitées sont regroupées directement de l'image du constructeur vers l'image d'exécution OSRM via un `Dockerfile.osrm` à plusieurs étapes.

`ghcr.io/project-osrm/osrm-backend` est actuellement disponible uniquement en `amd64`. Vérifiez l'architecture du daemon Docker actif avant de démarrer les services.

```bash
# Cibler l'hôte distant
export DOCKER_HOST=tcp://10.211.55.28:2375

# Vérifier l'hôte cible et l'architecture
make compose-doctor

# Construire et démarrer les services avec séquencement sûr + contrôles de santé
make compose-up

# Afficher les logs des services
make compose-logs

# Arrêter les services
make compose-down
```

Évitez d'exécuter `docker compose down & docker compose up --build` ; `&` exécute la première commande en arrière-plan et peut provoquer des conditions de course.

## Services Principaux

L'application encapsule la logique de routage complexe dans plusieurs services clés situés dans `src/app/services/` :

### 1. Client OSRM (`osrm_client.py`)
Un client HTTP asynchrone qui interagit directement avec le backend OSRM en C++. Il formate les requêtes et normalise les réponses.
**Exemple de Cas d'Utilisation** : Obtenir la géométrie exacte et les instructions de conduite pour un voyage entre un entrepôt et plusieurs points de livraison.

### 2. Constructeur de Graphes (`graph_builder.py`)
Transforme les matrices de distance et de durée brutes d'OSRM en graphes orientés `NetworkX`.
**Exemple de Cas d'Utilisation** : Générer une représentation mathématique du réseau routier pour alimenter des algorithmes d'optimisation avancés (comme des solveurs TSP personnalisés) ou pour identifier des nœuds isolés dans le réseau de livraison.

### 3. Service VRP (`vrp_service.py`)
Un solveur complet de Problèmes de Tournées de Véhicules (VRP). Il implémente une stratégie de Localisation-Attribution, affectant les arrêts de livraison à l'entrepôt (dépôt) disponible le plus proche et générant des séquences de livraison optimisées.
**Exemple de Cas d'Utilisation** : Une entreprise de logistique souhaite distribuer 500 colis par jour entre 5 chauffeurs partant de 2 entrepôts différents, en s'assurant que chaque chauffeur prenne le groupe d'arrêts le plus optimal.

## Exemples d'Utilisation pour les Applications Clientes

Voici des exemples montrant comment une application cliente peut interagir avec le microservice FastAPI en utilisant la bibliothèque `requests` de Python :

```python
import requests

BASE_URL = "http://localhost:8000"

# 1. Tracé d'Itinéraire (Route Plotting)
route_payload = {
    "origin": {"longitude": -84.0907, "latitude": 9.9281},
    "destination": {"longitude": -84.0833, "latitude": 9.9333},
    "alternatives": True
}
route_res = requests.post(f"{BASE_URL}/route", json=route_payload)

# 2. Point le Plus Proche (Alignement Routier)
nearest_payload = {
    "coordinate": {"longitude": -84.0907, "latitude": 9.9281},
    "number": 3
}
nearest_res = requests.post(f"{BASE_URL}/nearest", json=nearest_payload)

# 3. Problème du Voyageur de Commerce (TSP)
tsp_payload = {
    "coordinates": [
        {"longitude": -84.0907, "latitude": 9.9281},
        {"longitude": -84.0833, "latitude": 9.9333},
        {"longitude": -84.1107, "latitude": 9.9981}
    ]
}
tsp_res = requests.post(f"{BASE_URL}/trip", json=tsp_payload)

# 4. Regroupement (Clustering / Allocation)
cluster_payload = {
    "depots": [{"id": "D1", "longitude": -84.0907, "latitude": 9.9281}],
    "stops": [
        {"id": "L1", "longitude": -84.0833, "latitude": 9.9333},
        {"id": "L2", "longitude": -84.1107, "latitude": 9.9981}
    ],
    "vehicle_count": 2
}
cluster_res = requests.post(f"{BASE_URL}/vrp/allocate", json=cluster_payload)

# 5. Problème de Tournées de Véhicules (VRP)
vrp_payload = {
    "depots": [{"id": "D1", "longitude": -84.0907, "latitude": 9.9281}],
    "stops": [
        {"id": "L1", "longitude": -84.0833, "latitude": 9.9333},
        {"id": "L2", "longitude": -84.1107, "latitude": 9.9981}
    ],
    "vehicle_count": 2
}
vrp_res = requests.post(f"{BASE_URL}/vrp", json=vrp_payload)
```

## Outils de Visualisation

Le projet comprend des outils Python pour visualiser et comparer les itinéraires :

### Scripts d'Exemple

| Catégorie | Script | Ce Qu'il Démontre |
|----------|--------|---------------------|
| **Routage** | `visualize_routes.py` | Itinéraires principaux + alternatifs avec info-bulles de distance/durée |
| | `route_advanced_options.py` | Contraintes de cap, exclusion de voies, continue_straight, annotations d'étapes |
| | `error_handling_demo.py` | 8 scénarios d'erreur : 422, 429, erreurs de connexion, validation |
| | `matrix_example.py` | Tableau de matrice distance/durée entre plusieurs villes |
| | `matrix_graph_example.py` | Conversion de matrice en graphe avec attributs de nœuds/arêtes |
| | `nearest_example.py` | Ajustement au réseau routier avec plusieurs segments proches |
| | `match_example.py` | Appariement de traces GPS avec géométrie brute vs appariée |
| | `tile_example.py` | Téléchargement de Mapbox Vector Tile depuis `/tile` |
| **Benchmarking** | `compare_tsp.py` | Comparaison de séquence de livraison réelle vs optimisée par TSP |
| | `clustering_mode_comparison.py` | Comparaison des modes de clustering travel_time vs distance vs radial sur le même jeu de données |
| | `hysteresis_demo.py` | Tampon d'hystérésis empêchant le battement des affectations |
| **VRP** | `visualize_vrp.py` | VRP multi-dépôts avec itinéraires de véhicules codés par couleur |
| | `stress_test_vrp.py` | Test de charge avec 6 dépôts et 2500 arrêts |
| | `simple_id_example.py` | 10 véhicules, 300 arrêts avec des IDs personnalisés |
| | `run_clustering_workflow.py` | Clustering de 6500 arrêts, distance routière vs temps de trajet |
| **Infrastructure** | `health_and_metrics.py` | Sonde de santé, métriques Prometheus, cache, réessais, journalisation |

**Utilisation** :

```bash
# Ou lancer le menu interactif (découvre automatiquement tous les scripts)
uv run examples/main.py

# Exemples de routage
uv run examples/src/routing/matrix_example.py
uv run examples/src/routing/route_advanced_options.py
uv run examples/src/routing/error_handling_demo.py

# Exemples VRP
uv run examples/src/vrp/clustering_mode_comparison.py
uv run examples/src/vrp/hysteresis_demo.py
uv run examples/src/clustering/simple_id_example.py

# Infrastructure
uv run examples/src/infra/health_and_metrics.py

# Comparer les séquences réelles vs optimisées
uv run examples/src/benchmarking/compare_tsp.py
```

Les cartes sont enregistrées sous forme de fichiers HTML interactifs (`map.html`, `comparison_map.html`).

## Documentation API

La documentation interactive est disponible une fois que le service est en cours d'exécution :

- Swagger UI : `http://localhost:8000/docs`
- Redoc : `http://localhost:8000/redoc`

Pour un guide détaillé pour les développeurs, voir :

- [Référence API (Anglais)](docs/API_REFERENCE.md)
- [Referencia de la API (Espagnol)](docs/API_REFERENCE.es.md)
- [Référence API (Français)](docs/API_REFERENCE.fr.md)

## Composants

- **Moteur OSRM** : Moteur de routage C++ utilisant l'algorithme MLD.
- **FastAPI Gateway** : API Python asynchrone fournissant des points de terminaison spécialisés pour l'appariement de cartes, la génération de graphes et Problèmes de Tournées de Véhicules (VRP).
- **Résolveur VRP** : Moteur de Localisation-Attribution pour le regroupement multi-véhicules avec prise en charge des identifiants personnalisés et division d'itinéraires basée sur la capacité.
- **Intégration NetworkX** : Convertit de manière transparente les sorties de matrice en graphes sérialisables.
