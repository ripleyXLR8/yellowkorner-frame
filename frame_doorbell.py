"""Pop-up « On sonne à la porte » sur la Samsung The Frame, en mode Art : capture du portier GDS3710 dans une carte en
verre dépoli posée sur l'œuvre affichée, rafraîchie aussi vite que la TV suit, luminosité au maximum ; puis retour à
l'œuvre d'avant et à la luminosité d'avant au bout de DOORBELL_SECONDS. Une nouvelle sonnerie pendant ce temps relance
le délai. TV éteinte ou hors mode Art : rien.

    python frame_doorbell.py        lancé par le scénario Jeedom « Frame - pop-up portier » à chaque sonnerie
"""
import datetime, fcntl, io, json, os, subprocess, sys, time

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import yellowkorner_frame as yk

DURATION = int(os.environ.get('DOORBELL_SECONDS', '300'))
GDS_EQ = int(os.environ.get('GDS_EQ', '425'))   # équipement Jeedom du portier (plugin gds3710)
STATE, LOCK = yk.path('doorbell.json'), yk.path('doorbell.lock')
# Image affichée : œuvre + panneau (frame_panel.py), sinon l'œuvre seule (yellowkorner_frame.push()), sinon fond blanc.
# NB : si l'on choisit une autre œuvre à la main, la carte se pose sur la dernière photo de la semaine ;
# la relire sur la TV n'est pas possible (get_thumbnail de l'API art 2.03 ne répond jamais).
BASE = yk.path('display.jpg') if os.path.exists(yk.path('display.jpg')) else yk.path('current.jpg')


def log(msg):
    yk.log('portier : ' + msg)


def snapshot():
    """Image en direct par la session média du plugin gds3710 (son action « Prendre un snapshot » a échoué le
    2026-10-08 alors que la même requête, faite ainsi, passait)."""
    php = ('require "/var/www/html/core/php/core.inc.php"; $eq = eqLogic::byId(%d); $ck = $eq->sessionMedia();'
           '$ch = curl_init(); curl_setopt_array($ch, gds3710::optionsHttp(array(CURLOPT_URL => "https://"'
           ' . $eq->getConfiguration("ip") . "/snapshot/view0.jpg", CURLOPT_COOKIE => (string) $ck,'
           ' CURLOPT_HEADER => false, CURLOPT_RETURNTRANSFER => true, CURLOPT_TIMEOUT => 8)));'
           'echo curl_exec($ch);' % GDS_EQ)
    data = subprocess.run(['php', '-r', php], capture_output=True, timeout=30).stdout
    if data[:2] != b'\xff\xd8':
        raise RuntimeError('le portier n’a pas rendu d’image JPEG')
    return Image.open(io.BytesIO(data)).convert('RGB')


def font(name, size, wght):
    f = ImageFont.truetype(os.path.join(yk.FONTS, name), size)
    f.set_variation_by_axes([wght])
    return f


def text_layer(size, draw):
    """Calque RGBA avec le texte dessiné par draw(d) et son ombre douce (lisible sur un verre clair)."""
    layer = Image.new('RGBA', size, (0, 0, 0, 0))
    draw(ImageDraw.Draw(layer))
    shadow = Image.new('RGBA', size, (0, 0, 0, 0))
    shadow.putalpha(layer.getchannel('A').point(lambda a: a * 90 // 255).filter(ImageFilter.GaussianBlur(6)))
    return Image.alpha_composite(shadow, layer)


class Card:
    """Carte en verre dépoli centrée sur la photo, même verre que le panneau domotique (flou 38, voile 24 %).
    Tout ce qui ne change pas (œuvre, ombre, verre, titre) est préparé une fois ; chaque image n'y colle que
    la capture et l'heure."""
    CW, PAD, HEAD, R = 1300, 44, 120, 38
    CROP = (0.12, 0.03, 0.88, 0.97)   # fish-eye : retire une partie des bandes noires autour du cercle de l'objectif

    def __init__(self, cam_size):
        img = Image.open(BASE).convert('RGB') if os.path.exists(BASE) else Image.new('RGB', (3840, 2160), yk.MAT_RGB)
        W, H = img.size
        cw, ch = cam_size
        self.ch = round(self.CW * (ch * (self.CROP[3] - self.CROP[1])) / (cw * (self.CROP[2] - self.CROP[0])))
        PW, PH = self.CW + 2 * self.PAD, self.ch + self.HEAD + 2 * self.PAD
        x0, y0 = (W - PW) // 2, (H - 169 - PH) // 2   # centrée dans la fenêtre de la photo (légende dessous)
        box = (x0, y0, x0 + PW, y0 + PH)
        glass = Image.blend(img.crop(box).filter(ImageFilter.GaussianBlur(38)), Image.new('RGB', (PW, PH), (24, 24, 26)), 0.24)
        shade = Image.new('L', img.size, 0)   # ombre portée : la carte flotte au-dessus de l'œuvre
        ImageDraw.Draw(shade).rounded_rectangle((x0 + 10, y0 + 26, x0 + PW + 10, y0 + PH + 26), self.R, fill=120)
        img.paste((0, 0, 0), (0, 0), shade.filter(ImageFilter.GaussianBlur(40)))
        mask = Image.new('L', (PW, PH), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, PW - 1, PH - 1), self.R, fill=255)
        img.paste(glass, box[:2], mask)
        img = img.convert('RGBA')
        edge = Image.new('RGBA', img.size, (0, 0, 0, 0))
        ImageDraw.Draw(edge).rounded_rectangle(box, self.R, outline=(255, 255, 255, 46), width=2)
        img = Image.alpha_composite(img, edge)
        mid = self.PAD + self.HEAD // 2 - 6
        title = font('CormorantGaramond-Italic[wght].ttf', 76, 700)
        img.alpha_composite(text_layer((PW // 2 + 200, self.HEAD + 2 * self.PAD), lambda d: d.text(
            (self.PAD + 6, mid), 'On sonne à la porte', anchor='lm', fill=(255, 255, 255, 240), font=title)), (x0, y0))
        self.base = img.convert('RGB')
        self.cam_xy = (x0 + self.PAD, y0 + self.PAD + self.HEAD)
        self.clock_box = (x0 + PW // 2, y0, PW // 2, self.HEAD + 2 * self.PAD)   # x, y, largeur, hauteur
        self.cmask = Image.new('L', (self.CW, self.ch), 0)
        ImageDraw.Draw(self.cmask).rounded_rectangle((0, 0, self.CW - 1, self.ch - 1), 22, fill=255)
        self.clock = font('CormorantGaramond[wght].ttf', 34, 700)

    def frame(self, cam):
        cw, ch = cam.size
        cam = cam.crop((round(cw * self.CROP[0]), round(ch * self.CROP[1]), round(cw * self.CROP[2]), round(ch * self.CROP[3])))
        img = self.base.copy()
        img.paste(cam.resize((self.CW, self.ch), Image.LANCZOS), self.cam_xy, self.cmask)
        x, y, w, h = self.clock_box
        stamp = 'P O R T I E R   ·   ' + datetime.datetime.now(yk.TZ).strftime('%H:%M:%S')
        layer = text_layer((w, h), lambda d: d.text((w - self.PAD - 6, self.PAD + self.HEAD // 2 - 6), stamp, anchor='rm',
                                                     fill=(255, 255, 255, 150), features=['lnum'], font=self.clock))
        img.paste(layer, (x, y), layer)
        out = io.BytesIO()
        img.save(out, 'JPEG', quality=90)
        return out.getvalue()


def load():
    return json.load(open(STATE)) if os.path.exists(STATE) else None


def save(st):
    json.dump(st, open(STATE + '.tmp', 'w'))
    os.replace(STATE + '.tmp', STATE)


def end(tv, st):
    """Retour à l'œuvre d'avant (affichée seulement si la TV est encore en mode Art), luminosité d'avant,
    pop-up supprimé de la TV."""
    tv.ask('select_image', content_id=st['prev'], show=tv.ask('get_artmode_status').get('value') == 'on')
    tv.ask('set_brightness', value=int(st['brightness']))
    tv.ask('delete_image_list', content_id_list=[{'content_id': st['popup']}])
    log(f"fin : retour à {st['prev']}, luminosité {st['brightness']}, {st['popup']} supprimé")


def show(tv, card, cam):
    """Envoie une image du pop-up, l'affiche, puis supprime la précédente (l'état garde toujours celle affichée)."""
    cid = tv.upload(card.frame(cam))
    tv.ask('select_image', content_id=cid, show=True)
    with open(LOCK, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        st = load()
        old = st.get('popup')
        st['popup'] = cid
        save(st)
    if old and old != cid:
        tv.ask('delete_image_list', content_id_list=[{'content_id': old}])


def main():
    if not yk.tv_up():
        log('sonnerie, Frame éteinte : rien')
        return
    tv = yk.Frame()
    try:
        with open(LOCK, 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            st = load()
            if st and time.time() > st['until'] + 60:   # pop-up resté en plan (TV coupée pendant l'attente, plantage)
                try: end(tv, st)
                except Exception as e: log(f'nettoyage d’un ancien pop-up impossible : {e}')
                os.remove(STATE); st = None
            if st:   # déjà affiché : le processus en cours rafraîchit, on ne fait que relancer le délai
                st['until'] = time.time() + DURATION
                save(st)
                log('nouvelle sonnerie, délai relancé')
                return
            if tv.ask('get_artmode_status').get('value') != 'on':
                log('sonnerie, Frame hors mode Art : rien')
                return
            t = time.time()
            cam = snapshot()
            card = Card(cam.size)
            # Lire l'œuvre et la luminosité AVANT de poser le pop-up.
            st = {'prev': tv.ask('get_current_artwork')['content_id'], 'brightness': tv.ask('get_brightness')['value'],
                  'until': time.time() + DURATION}
            cid = tv.upload(card.frame(cam))
            tv.ask('select_image', content_id=cid, show=True)
            tv.ask('set_brightness', value=10)
            st['popup'] = cid
            save(st)
            log(f'pop-up {cid} affiché en {time.time() - t:.1f} s')
        # Rafraîchissement continu jusqu'à l'échéance ; hors mode Art (quelqu'un regarde la télé), on attend sans envoyer.
        n, t0 = 1, time.time()
        while time.time() < load()['until']:
            try:
                if tv.ask('get_artmode_status').get('value') != 'on':
                    time.sleep(5); continue
                show(tv, card, snapshot())
                n += 1
            except Exception as e:   # capture ratée ou connexion à la TV perdue : on reprend
                log(f'rafraîchissement : {e!r}')
                time.sleep(2)
                try: tv.close()
                except Exception: pass
                tv = yk.Frame()
        with open(LOCK, 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            st = load()
            end(tv, st)
            os.remove(STATE)
        log(f'{n} images en {time.time() - t0:.0f} s')
    finally:
        tv.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        log(f'échec : {e!r}')
        sys.exit(1)
