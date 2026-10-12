# yellowkorner-frame

Une photo Yellow Korner par semaine sur une Samsung The Frame, en local, sans SmartThings ni jumelage.

- **`yellowkorner_frame.py`** : choisit un best-seller Yellow Korner (« Œuvres plébiscitées », en paysage, pas deux fois en 14 jours), l'agrandit ×4 par imgupscaler.com, dessine un passe-partout blanc légendé (titre en italique, artiste) et l'envoie en 4K sur la Frame.
- **`frame_doorbell.py`** : à chaque sonnerie du portier, affiche la caméra (Grandstream GDS3710, plugin Jeedom `gds3710`) dans une carte en verre dépoli posée sur l'œuvre, puis remet l'œuvre au bout de 5 min.
- **`frame_panel.py`** : panneau domotique (météo, heure, températures, solaire) dessiné sur l'œuvre. Il est désactivé chez moi, mais fonctionne.

Les deux derniers scripts sont écrits pour tourner **dans le conteneur Jeedom** : ils lisent les commandes par `php -r` et le cœur de Jeedom. Le premier n'a besoin que de Python.

## Installation

```sh
python3 -m venv venv && venv/bin/pip install -r requirements.txt
FRAME_IP=192.168.1.50 FRAME_MAC=AA:BB:CC:DD:EE:FF BROADCAST=192.168.1.255 venv/bin/python yellowkorner_frame.py
```

Lancez-le toutes les 5 min (scénario Jeedom ou cron). Chaque lundi à 5 h, il prépare la photo, puis il la pose au premier passage où la TV répond. `yellowkorner_frame.py now` change la photo tout de suite, en réveillant la TV par Wake-on-LAN (utile pour une routine « Alexa, change le tableau »).

Les variables d'environnement sont lues en tête de chaque script : `RUN_DAY`, `RUN_AT`, `REPEAT_DAYS`, `MIN_RATIO`, `MAT_MARGIN`, `DATA_DIR`, `DOORBELL_SECONDS`, `GDS_EQ`. Les identifiants des commandes Jeedom du panneau sont dans `frame_panel.py`.

## Ce que la Frame ne dit pas

Ces points ont été relevés sur une QE50LS03A de 2021 (API art 2.03) :

- **Le canal Art** (`wss://IP:8002/api/v2/channels/com.samsung.art-app`) ne demande aucune autorisation. **Le canal télécommande**, lui, demande un jeton, que la TV lie à l'adresse IP du client.
- **`send_image`** : le `conn_info.id` doit être le même uuid que l'`id` de la requête. Sinon la TV répond `ready_to_use`, accepte les octets, puis les jette sans erreur.
- **Passe-partout** : `none` n'est accepté que pour une image 16:9 exacte (sinon erreur -7). `change_matte` avec `portrait_matte_id` ignore `matte_id`, sans le signaler.
- **`get_thumbnail`** ne répond jamais et bloque le client.
- **N'éteignez jamais la TV par un appui long sur Power** : c'est une extinction complète. Elle coupe le réveil par le capteur de présence, et la TV ne revient plus d'elle-même en mode Art. La nuit, elle dort déjà toute seule (veille du mode Art).
- **Wake-on-LAN** la rallume en mode Art.

## Avertissement

Ce dépôt est un projet personnel, sans lien avec Yellow Korner ni avec imgupscaler.com. Les photos appartiennent à leurs auteurs. Le script prend l'image de la fiche produit, celle qui n'a pas de filigrane, mais il ne retire jamais un filigrane. imgupscaler.com n'a pas d'API publique : le script appelle les points d'accès de son site, dans la limite de l'offre gratuite, et il peut cesser de fonctionner à tout moment. À réserver à un usage privé.

## Licence

Code : MIT. Polices Cormorant Garamond : SIL Open Font License (`fonts/OFL.txt`).
