# Microservice Backend OSRM

[English](https://github.com/lvalverde/osrm-microservice/blob/main/README.md) | [Español](https://github.com/lvalverde/osrm-microservice/blob/main/README.es.md) | [Français](https://github.com/lvalverde/osrm-microservice/blob/main/README.fr.md)

Routage haute performance et appariement de cartes (map-matching) pour le Costa Rica.

## Déploiement

Le projet prend en charge **deux** options de déploiement. Les deux exécutent les
mêmes trois services — le moteur OSRM, un cache Redis et la passerelle FastAPI —
et tout ce dont l'une ou l'autre a besoin se trouve dans [`deploy/`](deploy).

| Option | Fichiers | Commencer par | Quand |
|---|---|---|---|
| **Docker** | [`deploy/docker/`](deploy/docker) | `make compose-up` | N'importe quel hôte Docker Linux, local ou distant |
| **Prison FreeBSD** | [`deploy/freebsd/`](deploy/freebsd) | `make jail-up` | Une prison sur un hôte FreeBSD, qui ne peut pas exécuter Docker |

Les instructions complètes pour les deux, y compris les prérequis et les notes sur
Apple Silicon, se trouvent dans **[docs/deployment.md](docs/deployment.md)**.

### Docker, en bref

Les données sont traitées en une image sur votre machine puis regroupées dans
l'image d'exécution par le `deploy/docker/Dockerfile.osrm` à plusieurs étapes :
rien n'est monté et la pile peut être déployée telle quelle sur un hôte Docker
distant.

```bash
make download-data              # télécharger l'extrait du Costa Rica dans ./data
make process-osrm PROFILE=car   # extract / partition / customize

export DOCKER_HOST=tcp://10.211.55.28:2375   # optionnel : cibler un daemon distant
make compose-doctor             # afficher l'hôte Docker actif et son architecture
make compose-up                 # construire et démarrer, avec séquencement et contrôles de santé
make compose-logs
make compose-down
```

Évitez d'exécuter `docker compose down & docker compose up --build` ; `&` exécute
la première commande en arrière-plan et peut provoquer des conditions de course.

### Prison FreeBSD, en bref

Une prison ne peut pas exécuter Docker — les prisons partagent le noyau FreeBSD et
Docker a besoin des namespaces et cgroups Linux — donc les mêmes services
s'exécutent nativement à partir de paquets et de scripts rc.d. Voir
[docs/deployment_freebsd.md](docs/deployment_freebsd.md).

```bash
make jail-doctor      # vérifier la cible et comment élever les privilèges
make jail-bootstrap   # paquets et utilisateur de service
make jail-data        # construire les données OSRM dans la prison
make jail-up          # déployer la passerelle et démarrer tous les services
```

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

## Documentation des Fonctionnalités

| Fonctionnalité | Description |
|----------------|-------------|
| [Cache des Réponses](docs/features/caching.md) | Stratégie Cache-Aside L1/L2 avec couches mémoire et Redis. |
| [Modes de Regroupement VRP](docs/features/clustering_modes.md) | Allocation `travel_time`, `distance` et `radial` avec hystérésis. |
| [Observabilité](docs/features/observability.md) | Journalisation structurée, métriques Prometheus, tracing OpenTelemetry, santé. |
| [Limitation de Débit](docs/features/rate_limiting.md) | Limites de requêtes par endpoint et configuration. |
| [Référence de Configuration](docs/configuration.md) | Liste complète des variables d'environnement. |

## Composants

- **Moteur OSRM** : Moteur de routage C++ utilisant l'algorithme MLD.
- **FastAPI Gateway** : API Python asynchrone fournissant des points de terminaison spécialisés pour l'appariement de cartes, la génération de graphes et Problèmes de Tournées de Véhicules (VRP).
- **Résolveur VRP** : Moteur de Localisation-Attribution pour le regroupement multi-véhicules avec prise en charge des identifiants personnalisés et division d'itinéraires basée sur la capacité.
- **Intégration NetworkX** : Convertit de manière transparente les sorties de matrice en graphes sérialisables.
