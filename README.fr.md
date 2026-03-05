# Microservice Backend OSRM

Routage haute performance et appariement de cartes (map-matching) pour le Costa Rica.

## Instructions de Configuration

Ce projet utilise un flux de travail de **Construction Locale et Transfert Groupé** pour prendre en charge le déploiement sur des hôtes Docker distants tout en traitant les données localement sur macOS.

### 1. Prérequis

- Docker Desktop (macOS)
- Hôte Docker Distant (ex: VM Linux à `10.211.55.28`)
- `make`

### 2. Acquisition de Données et Traitement Local

Extrayez et traitez les données OSM du Costa Rica localement. Ce processus regroupe les données dans votre dossier local `./data` en utilisant un constructeur basé sur Docker "Sans Montage" pour contourner les restrictions du système de fichiers macOS.

```bash
# Télécharger les dernières données cartographiques du Costa Rica
make download-data

# Traiter les données localement (sans utiliser de volumes)
make process-osrm
```

### 3. Déploiement Distant

Déployez l'API et le moteur OSRM sur l'hôte distant. Les données traitées sont regroupées dans l'image OSRM pendant le processus de construction et transférées via le contexte de construction Docker.

```bash
# Cibler l'hôte distant
export DOCKER_HOST=tcp://10.211.55.28:2375

# Construire et démarrer les services
docker compose up -d --build
```

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
- **FastAPI Gateway** : API Python asynchrone fournissant des points de terminaison spécialisés pour l'appariement de cartes et la génération de graphes.
- **Intégration NetworkX** : Convertit de manière transparente les sorties de matrice en graphes sérialisables.
