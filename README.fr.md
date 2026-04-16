# Microservice Backend OSRM

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

```bash
# Cibler l'hôte distant
export DOCKER_HOST=tcp://10.211.55.28:2375

# Construire et démarrer les services
docker compose up -d --build
```

## Services Principaux

L'application encapsule la logique de routage complexe dans plusieurs services clés situés dans `app/services/` :

### 1. Client OSRM (`osrm_client.py`)
Un client HTTP asynchrone qui interagit directement avec le backend OSRM en C++. Il formate les requêtes et normalise les réponses.
**Exemple de Cas d'Utilisation** : Obtenir la géométrie exacte et les instructions de conduite pour un voyage entre un entrepôt et plusieurs points de livraison.

### 2. Constructeur de Graphes (`graph_builder.py`)
Transforme les matrices de distance et de durée brutes d'OSRM en graphes orientés `NetworkX`.
**Exemple de Cas d'Utilisation** : Générer une représentation mathématique du réseau routier pour alimenter des algorithmes d'optimisation avancés (comme des solveurs TSP personnalisés) ou pour identifier des nœuds isolés dans le réseau de livraison.

### 3. Service VRP (`vrp_service.py`)
Un solveur complet de Problèmes de Tournées de Véhicules (VRP). Il implémente une stratégie de Localisation-Attribution, affectant les arrêts de livraison à l'entrepôt (dépôt) disponible le plus proche et générant des séquences de livraison optimisées.
**Exemple de Cas d'Utilisation** : Une entreprise de logistique souhaite distribuer 500 colis par jour entre 5 chauffeurs partant de 2 entrepôts différents, en s'assurant que chaque chauffeur prenne le groupe d'arrêts le plus optimal.

## Outils de Visualisation

Le projet comprend des outils Python pour visualiser et comparer les itinéraires :

- **`visualize_routes.py`** : Récupère et trace les itinéraires principaux et alternatifs pour un voyage.
- **`compare_tsp.py`** : Compare une séquence d'arrêts fournie (Réel) à un voyage aller-retour optimisé par TSP (Optimisé).
- **`examples/clustering/simple_id_example.py`** : Un démonstrateur VRP complet qui simule 10 véhicules sur plusieurs dépôts et génère une carte Folium interactive.

**Utilisation** :

```bash
# Exécuter la simulation VRP de 10 véhicules
uv run examples/clustering/simple_id_example.py

# Comparer les séquences réelles vs optimisées
uv run compare_tsp.py
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
