"""Chaque semaine (RUN_DAY à RUN_AT), affiche sur la Samsung The Frame un best-seller Yellow Korner
(pas deux fois en REPEAT_DAYS jours), agrandi ×4 par l'IA d'imgupscaler.com, dans un passe-partout blanc
dessiné dans l'image avec le titre et l'artiste (compose), envoyé en 4K sans passe-partout de la TV.

    python yellowkorner_frame.py        un passage (scénario Jeedom toutes les 5 min)
    python yellowkorner_frame.py now    nouvelle photo tout de suite (« Alexa, change le tableau »)
"""
import base64, datetime, io, json, os, random, re, socket, ssl, sys, time, uuid
from zoneinfo import ZoneInfo

import requests
import websocket
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

FRAME_IP = os.environ.get('FRAME_IP', '192.168.1.50')
FRAME_MAC = os.environ.get('FRAME_MAC', 'AA:BB:CC:DD:EE:FF')
BROADCAST = os.environ.get('BROADCAST', '192.168.1.255')
RUN_AT = os.environ.get('RUN_AT', '05:00')
RUN_DAY = int(os.environ.get('RUN_DAY', '0'))   # jour du changement automatique : 0 = lundi … 6 = dimanche
TZ = ZoneInfo(os.environ.get('TZ', 'Europe/Paris'))
# Passe-partout de la TV : aucun, il est dessiné dans l'image (compose). « none » n'est accepté que pour une
# image 16:9 exacte (sinon erreur -7) ; celui des portraits ne peut pas être « none », d'où flexible_polar.
MATTE, PORTRAIT_MATTE = os.environ.get('MATTE', 'none'), 'flexible_polar'
# Largeur / hauteur minimale : la photo est recadrée au format de la fenêtre du passe-partout (≈ 2:1) ;
# une 3:2 (1,5) y perd un quart de sa hauteur, une 4:3 un tiers. 41 best-sellers sur 91 passent ce seuil.
MIN_RATIO = float(os.environ.get('MIN_RATIO', '1.45'))
DATA = os.environ.get('DATA_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
REPEAT_DAYS = int(os.environ.get('REPEAT_DAYS', '14'))   # délai minimal avant de remontrer une photo
BEST_SELLERS = 'https://www.yellowkorner.com/fr/photographies-dart/oeuvres-plebiscitees/?start={}&sz=60'
IU = 'https://imgupscaler.com/api/legacy/'
UA = {'User-Agent': 'Mozilla/5.0'}


def log(msg):
    print(f'{datetime.datetime.now(TZ):%Y-%m-%d %H:%M:%S} {msg}', flush=True)


def get(url):
    r = requests.get(url, headers=UA, timeout=60)
    r.raise_for_status()
    return r.content


def catalogue():
    """Les best-sellers : catégorie « Œuvres plébiscitées », 60 par page (91 en octobre 2026, dont 46 en paysage).
    Chaque vignette de la grille porte un bloc JSON ; le reste de la page (bandeaux) est ignoré.
    Renvoie {url de la vignette: (titre, artiste)} ; l'artiste est parfois une agence (« BRIDGEMAN IMAGES »)."""
    works = {}
    for start in range(0, 1200, 60):
        page = get(BEST_SELLERS.format(start)).decode()
        before = len(works)
        for b in re.findall(r'<script data-product-id="\d+" type="application/json">\s*(\{.*?\})\s*</script>',
                            page, re.S):
            d = json.loads(b)
            works.setdefault(d['images']['all'][0]['url'], (d['productName'], (d.get('artist') or {}).get('name', '')))
        if len(works) == before:
            break
    if not works:
        raise RuntimeError('aucun best-seller trouvé : la page Yellow Korner a changé ?')
    return works


def recent():
    """Photos affichées depuis moins de REPEAT_DAYS jours (history.txt : « AAAA-MM-JJ url » par ligne)."""
    limit = (datetime.datetime.now(TZ).date() - datetime.timedelta(days=REPEAT_DAYS)).isoformat()
    return [line for line in read('history.txt').splitlines() if line[:10] > limit]


def choose(works):
    """Un best-seller non affiché depuis REPEAT_DAYS jours, en paysage de préférence (format jugé sur la vignette)."""
    shown = {line[11:] for line in recent()}
    urls = [u for u in works if u not in shown]
    if not urls:
        raise RuntimeError(f'tous les best-sellers ont été affichés depuis moins de {REPEAT_DAYS} jours')
    random.shuffle(urls)
    pick = urls[0]
    for u in urls:
        try:
            w, h = Image.open(io.BytesIO(get(u))).size
        except Exception as e:
            log(f'  {u} : {e}'); continue
        if w / h >= MIN_RATIO:
            pick = u
            break
    return pick


def upscale(data):
    """×4 par imgupscaler.com : gratuit sans compte, une image à la fois, comme sur son site (~20-40 s)."""
    h = {**UA, 'Origin': 'https://imgupscaler.com', 'Referer': 'https://imgupscaler.com/'}
    r = requests.post(IU + 'upload', headers=h, data={'tool': 'upscaler', 'scaleRadio': '4'},
                      files={'file': ('photo.jpg', data, 'image/jpeg')}, timeout=120)
    if not r.ok:
        raise RuntimeError(f'envoi refusé ({r.status_code}) : {r.text[:200]}')
    task = r.json()['taskId']
    for _ in range(60):
        time.sleep(10)
        s = requests.post(IU + 'status', headers=h, json={'tool': 'upscaler', 'taskId': task, 'scaleRadio': '4'},
                          timeout=60).json()
        if s.get('status') in ('success', 'completed'):
            return get(s['downloadUrls'][0])
        if s.get('status') in ('failed', 'nsfw') or s.get('error'):
            raise RuntimeError(f'échec : {s}')
    raise RuntimeError('pas de résultat après 10 min')


class Frame:
    """Mode Art de la Frame (websocket local, port 8002, sans jumelage sur ce modèle)."""

    def __init__(self):
        name = base64.b64encode(b'YellowKorner').decode()
        self.ws = websocket.create_connection(
            f'wss://{FRAME_IP}:8002/api/v2/channels/com.samsung.art-app?name={name}',
            sslopt={'cert_reqs': ssl.CERT_NONE}, timeout=60)
        while json.loads(self.ws.recv()).get('event') not in ('ms.channel.ready', 'ms.channel.connect'):
            pass

    def wait(self, rid=None, event=None):
        """Prochain message de l'appli Art qui répond à `rid`, ou qui porte l'événement `event`."""
        while True:
            m = json.loads(self.ws.recv())
            if m.get('event') != 'd2d_service_message':
                continue
            d = json.loads(m['data'])
            mine = rid in (d.get('id'), d.get('request_id'))
            if d.get('event') == 'error' and mine:
                raise RuntimeError(f'refus de la TV : {d}')
            if (event and d.get('event') == event) or (not event and mine):
                return d

    def ask(self, req, event=None, rid=None, **kw):
        rid = rid or str(uuid.uuid4())
        self.ws.send(json.dumps({'method': 'ms.channel.emit', 'params': {'event': 'art_app_request', 'to': 'host',
            'data': json.dumps({'request': req, 'id': rid, 'request_id': rid, **kw})}}))
        return self.wait(rid, event)

    def upload(self, data):
        # Le conn_info.id DOIT être l'id de la requête, sinon la TV accepte les octets puis les jette sans erreur.
        rid = str(uuid.uuid4())
        self.ws.settimeout(30)
        r = self.ask('send_image', 'ready_to_use', rid, file_type='jpg', file_size=len(data),
                     matte_id=MATTE, portrait_matte_id=PORTRAIT_MATTE,
                     image_date=datetime.datetime.now(TZ).strftime('%Y:%m:%d %H:%M:%S'),
                     conn_info={'d2d_mode': 'socket', 'connection_id': random.randrange(4 << 30), 'id': rid})
        ci = json.loads(r['conn_info'])
        head = json.dumps({'num': 0, 'total': 1, 'fileLength': len(data), 'fileName': 'yk', 'fileType': 'jpg',
                           'secKey': ci['key'], 'version': '0.0.1'}).encode()
        s = socket.create_connection((ci['ip'], int(ci['port'])), timeout=60)
        if ci.get('secured'):
            ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
            s = ctx.wrap_socket(s)
        try:
            s.sendall(len(head).to_bytes(4, 'big') + head + data)
            s.shutdown(socket.SHUT_WR)
            self.ws.settimeout(90)
            return self.wait(event='image_added')['content_id']
        finally:
            s.close()

    def close(self):
        self.ws.close()


def path(name):
    return os.path.join(DATA, name)


def read(name):
    return open(path(name), encoding='utf-8').read().strip() if os.path.exists(path(name)) else ''


def write(name, text):
    open(path(name), 'w', encoding='utf-8').write(text)


FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts')   # Cormorant Garamond, licence OFL
MAT_RGB = (246, 245, 242)   # blanc de galerie (le « polar » de la Frame, 232-230-231, jugé trop gris)
MAT_MARGIN = int(os.environ.get('MAT_MARGIN', '70'))   # 190 puis 110 jugés trop larges


def edge_shade(size, depth, top=0, bottom=0, left=0, right=0):
    """Masque d'ombre en dégradé le long des bords (opacité 0-255 par côté). Les côtés sont combinés
    par maximum, ce qui donne des coins propres, sans marche."""
    W, H = size
    col = Image.new('L', (1, depth))
    col.putdata([int(255 * (1 - i / depth) ** 2) for i in range(depth)])   # du bord vers l'intérieur
    row = col.transpose(Image.TRANSPOSE)
    mask = Image.new('L', size, 0)
    for strength, ramp, pos in ((top, col.resize((W, depth)), (0, 0)),
                                (bottom, col.resize((W, depth)).transpose(Image.FLIP_TOP_BOTTOM), (0, H - depth)),
                                (left, row.resize((depth, H)), (0, 0)),
                                (right, row.resize((depth, H)).transpose(Image.FLIP_LEFT_RIGHT), (W - depth, 0))):
        if strength:
            layer = Image.new('L', size, 0)
            layer.paste(ramp.point(lambda v: v * strength // 255), pos)
            mask = ImageChops.lighter(mask, layer)
    return mask


def compose(photo, title, artist, size=(3840, 2160), margin=MAT_MARGIN, ratio=None):
    """Passe-partout dessiné dans l'image, avec la légende : la TV ne sait pas écrire dans les siens.
    L'image est envoyée à la TV sans passe-partout (« none », 16:9).
    `margin` : bande minimale de passe-partout autour du bloc photo + légende, en pixels 4K.
    `ratio` : None garde le cadrage d'origine ; un nombre (16/9) recadre la photo au centre à ce format ;
    'box' la recadre au format de la fenêtre disponible, pour des bandes égales tout autour."""
    W, H = size
    canvas = Image.new('RGB', size, MAT_RGB)
    draw = ImageDraw.Draw(canvas)
    ts, as_ = 64, 44   # tailles du titre et de l'artiste
    f_title = ImageFont.truetype(os.path.join(FONTS, 'CormorantGaramond-Italic[wght].ttf'), ts)
    f_artist = ImageFont.truetype(os.path.join(FONTS, 'CormorantGaramond[wght].ttf'), as_)
    f_artist.set_variation_by_axes([500])
    gap, line = 45, 16
    caption = ts + line + as_
    box_w, box_h = W - 2 * margin, H - 2 * margin - gap - caption
    if ratio:
        r = box_w / box_h if ratio == 'box' else ratio
        tw = min(box_w, box_h * r)
        photo = ImageOps.fit(photo, (round(tw), round(tw / r)), Image.LANCZOS)
    else:
        scale = min(box_w / photo.width, box_h / photo.height)
        photo = photo.resize((round(photo.width * scale), round(photo.height * scale)), Image.LANCZOS)
    w, h = photo.size
    x, y = (W - w) // 2, (H - h - gap - caption) // 2
    # Biseau : l'arête blanche de la fenêtre taillée dans le carton.
    draw.rectangle((x - 9, y - 9, x + w + 8, y + h + 8), fill=(255, 255, 255))
    canvas.paste(photo, (x, y))
    # Ombre du passe-partout sur la photo (lumière venant d'en haut), puis ombre du cadre de la TV
    # sur le bord extérieur du passe-partout, comme sur ceux de la Frame.
    canvas.paste((0, 0, 0), (x, y), edge_shade((w, h), 28, top=70, left=42))
    canvas.paste((0, 0, 0), (0, 0), edge_shade(size, 46, top=60, bottom=22, left=40, right=40))
    ty = y + h + gap
    draw.text((W // 2, ty), title, font=f_title, fill=(58, 56, 53), anchor='mt')
    draw.text((W // 2, ty + ts + line), artist, font=f_artist, fill=(118, 115, 110), anchor='mt')
    return canvas


def prepare(url=None):
    """Choisit (ou reprend `url`) et met en page la photo, gardée dans pending.jpg jusqu'à ce que la TV réponde.
    Source : la version « pdp » (1000 px) de la fiche produit ; la « max » (2000 px) porte un filigrane
    YELLOW KORNER : ne pas la prendre."""
    works = catalogue()
    url = url or choose(works)
    title, artist = works.get(url, ('', ''))
    log(f'photo : {url} — {title} / {artist}')
    data = get(url.replace('/plp/', '/pdp/'))
    t = time.time()
    try:
        data = upscale(data)
        log(f'agrandie par imgupscaler en {time.time() - t:.0f} s')
    except Exception as e:   # une photo non agrandie vaut mieux que pas de photo du jour
        log(f'agrandissement impossible, photo d’origine : {e}')
    img = compose(Image.open(io.BytesIO(data)).convert('RGB'), title, artist, ratio='box')
    img.save(path('pending.jpg'), 'JPEG', quality=92)
    write('pending_url', url)
    log(f'prête : {img.width}x{img.height}')


def tv_up():
    try:
        socket.create_connection((FRAME_IP, 8001), timeout=3).close()
        return True
    except OSError:
        return False


def push():
    """Envoie pending.jpg ; ne bascule l'écran sur l'œuvre que si la TV est déjà en mode Art."""
    tv = Frame()
    try:
        cid = tv.upload(open(path('pending.jpg'), 'rb').read())
        art = tv.ask('get_artmode_status').get('value') == 'on'
        tv.ask('select_image', content_id=cid, show=art)
        log(f'posée : {cid}' + ('' if art else ' (affichée au prochain mode Art)'))
        # On ne supprime que l'image posée par ce script la fois précédente, jamais une œuvre posée à la main.
        prev = read('last_content_id')
        if prev and prev != cid:
            try:
                tv.ask('delete_image_list', content_id_list=[{'content_id': prev}])
                log(f'précédente supprimée : {prev}')
            except Exception as e:
                log(f'précédente non supprimée ({prev}) : {e}')
    finally:
        tv.close()
    write('last_content_id', cid)
    write('history.txt', '\n'.join(recent() + [f'{datetime.datetime.now(TZ).date()} {read("pending_url")}']))
    os.replace(path('pending.jpg'), path('current.jpg'))   # fond du pop-up portier (frame_doorbell.py)


def wake():
    """Wake-on-LAN (la TV s'allume en mode Art), puis attend qu'elle réponde, 2 min au plus."""
    packet = b'\xff' * 6 + bytes.fromhex(FRAME_MAC.replace(':', '')) * 16
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    for addr in (BROADCAST, '255.255.255.255'):
        for port in (7, 9):
            s.sendto(packet, (addr, port))
    s.close()
    for _ in range(24):
        time.sleep(5)
        if tv_up():
            time.sleep(10)   # laisser l'appli Art démarrer
            return True
    log('la Frame ne s’est pas réveillée')
    return False


def deliver(wake_up=False):
    """Pose pending.jpg quand la TV répond. Jamais d'extinction par le script : un appui long sur Power
    l'éteint complètement et coupe le réveil par le capteur de présence (vu le 2026-10-12). Sans réveil,
    la photo part au premier passage où la Frame est allumée ; seul « change le tableau » la réveille."""
    if tv_up() or (wake_up and wake()):
        push()


def last_slot(now):
    """Dernier créneau de changement passé : le jour RUN_DAY à RUN_AT (le lundi à 5 h par défaut)."""
    hh, mm = map(int, RUN_AT.split(':'))
    slot = (now - datetime.timedelta(days=(now.weekday() - RUN_DAY) % 7)).replace(
        hour=hh, minute=mm, second=0, microsecond=0)
    return slot - datetime.timedelta(days=7) if slot > now else slot


def main():
    """Un passage, lancé toutes les 5 min par un scénario Jeedom : prépare la photo de la semaine dès que le
    créneau est passé (rattrapé au passage suivant s'il a été manqué), puis la pose dès que la Frame répond."""
    os.makedirs(DATA, exist_ok=True)
    if sys.argv[1:] == ['now']:   # « Alexa, change le tableau » (scénario 99) : on veut le voir, la TV reste allumée
        log('nouveau tableau demandé')
        prepare(); deliver(wake_up=True); return
    now = datetime.datetime.now(TZ)
    try:
        if read('prepared_on') < last_slot(now).date().isoformat():
            prepare()
            write('prepared_on', now.date().isoformat())
        if os.path.exists(path('pending.jpg')):
            deliver()
    except Exception as e:
        log(f'échec : {e!r}')
        sys.exit(1)


if __name__ == '__main__':
    main()
