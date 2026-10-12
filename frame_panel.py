"""Panneau domotique en verre dépoli, en haut à gauche de l'œuvre de la semaine sur la Samsung The Frame : météo,
heure, température intérieure moyenne, production solaire, consommation. Redessiné et renvoyé chaque minute pour que
l'heure soit juste (scénario Jeedom « Frame - panneau domotique », toutes les minutes).

Ne fait rien : TV éteinte ou hors mode Art, pop-up du portier en cours, ou autre œuvre choisie à la main.
Ne supprime que ses propres images (panel_cid), jamais l'œuvre posée par yellowkorner_frame.py.

    python frame_panel.py
"""
import datetime, fcntl, json, os, subprocess, sys, time

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import yellowkorner_frame as yk

ART = yk.path('current.jpg')        # œuvre de la semaine sans panneau, gardée par yellowkorner_frame.push()
DISPLAY = yk.path('display.jpg')    # œuvre + panneau telle qu'affichée : fond du pop-up du portier
LOCK = yk.path('doorbell.lock')     # partagé avec frame_doorbell.py : jamais deux envois croisés
CMDS = dict(ext=389735, cond=389745, tmin=389743, tmax=389744,    # [Jardin][Météo Locale]
            prod=378402, conso=378458)                            # Envoy : Prod Inst, Conso Total Inst (W)
ROOMS = [48, 291, 335996, 1307, 1364]   # thermostats dont on fait la moyenne


def log(msg):
    yk.log('panneau : ' + msg)


def values():
    ids = list(CMDS.values()) + ROOMS
    php = ('require "/var/www/html/core/php/core.inc.php"; $o = array();'
           'foreach (array(%s) as $i) { $o[$i] = cmd::byId($i)->execCmd(); } echo json_encode($o);' % ','.join(map(str, ids)))
    v = {int(k): x for k, x in json.loads(subprocess.check_output(['php', '-r', php], timeout=30)).items()}
    return {k: v[i] for k, i in CMDS.items()}, sum(float(v[i]) for i in ROOMS) / len(ROOMS)


def font(name, size, wght):
    f = ImageFont.truetype(os.path.join(yk.FONTS, name), size)
    f.set_variation_by_axes([wght])
    return f


def render():
    v, inside = values()
    fr = lambda x, d=1: ('%.*f' % (d, float(x))).replace('.', ',')
    kw = lambda w: fr(float(w) / 1000) + ' kW'
    img = Image.open(ART).convert('RGB')
    W, H = img.size
    # Fenêtre de la photo dans le passe-partout dessiné par compose(ratio='box') : 70 px de marge.
    PW, PH, INSET, R = 960, 400, 64, 34
    x0, y0 = 70 + INSET, 70 + INSET
    box = (x0, y0, x0 + PW, y0 + PH)
    glass = Image.blend(img.crop(box).filter(ImageFilter.GaussianBlur(38)), Image.new('RGB', (PW, PH), (24, 24, 26)), 0.24)
    mask = Image.new('L', (PW, PH), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, PW - 1, PH - 1), R, fill=255)
    img.paste(glass, box[:2], mask)
    layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    text = lambda *a, **k: d.text(*a, features=['lnum'], **k)   # chiffres alignés, pas elzéviriens
    d.rounded_rectangle(box, R, outline=(255, 255, 255, 46), width=2)
    WHITE, SOFT, FAINT = (255, 255, 255, 235), (255, 255, 255, 170), (255, 255, 255, 140)
    lab = font('CormorantGaramond[wght].ttf', 30, 700)
    lx, ty = x0 + 58, y0 + 36
    text((lx, ty), fr(v['ext'], 0) + '°', font=font('CormorantGaramond[wght].ttf', 140, 600), fill=WHITE)
    text((lx + 230, ty + 30), str(v['cond']), font=font('CormorantGaramond-Italic[wght].ttf', 54, 700), fill=WHITE)
    text((lx + 230, ty + 98), '%s° · %s°' % (fr(v['tmin'], 0), fr(v['tmax'], 0)),
         font=font('CormorantGaramond[wght].ttf', 38, 700), fill=SOFT)
    text((x0 + PW - 58, ty + 30), datetime.datetime.now(yk.TZ).strftime('%H:%M'),
         font=font('CormorantGaramond[wght].ttf', 66, 700), fill=SOFT, anchor='ra')
    ly = y0 + 212
    d.line((lx, ly, x0 + PW - 58, ly), fill=(255, 255, 255, 60), width=2)
    val = font('CormorantGaramond[wght].ttf', 60, 700)
    for n, (l, x) in enumerate([('INTÉRIEUR', fr(inside) + '°'), ('SOLEIL', kw(v['prod'])), ('MAISON', kw(v['conso']))]):
        cx = lx + n * (PW - 116) / 3
        text((cx, ly + 36), ' '.join(l), font=lab, fill=FAINT)
        text((cx, ly + 78), x, font=val, fill=WHITE)
    # Ombre douce sous le texte : garde la lecture sur un fond clair.
    shadow = Image.new('RGBA', img.size, (0, 0, 0, 0))
    shadow.putalpha(layer.getchannel('A').point(lambda a: a * 90 // 255).filter(ImageFilter.GaussianBlur(6)))
    img = Image.alpha_composite(Image.alpha_composite(img.convert('RGBA'), shadow), layer).convert('RGB')
    img.save(DISPLAY + '.tmp.jpg', 'JPEG', quality=93)
    return DISPLAY + '.tmp.jpg'


def main():
    if not os.path.exists(ART) or not yk.tv_up():
        return
    with open(LOCK, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if os.path.exists(yk.path('doorbell.json')):   # pop-up du portier affiché : il reprendra la main après
            return
        tv = yk.Frame()
        try:
            if tv.ask('get_artmode_status').get('value') != 'on':
                return
            mine, art = yk.read('panel_cid'), yk.read('last_content_id')
            cur = tv.ask('get_current_artwork')['content_id']
            if cur not in (mine, art):   # œuvre choisie à la main : ne pas la remplacer chaque minute
                return
            t = time.time()
            path = render()
            cid = tv.upload(open(path, 'rb').read())
            tv.ask('select_image', content_id=cid, show=True)
            yk.write('panel_cid', cid)
            os.replace(path, DISPLAY)
            if mine and mine not in (cid, art):
                tv.ask('delete_image_list', content_id_list=[{'content_id': mine}])
            if time.time() - t > 20:
                log(f'{cid} posé en {time.time() - t:.1f} s (lent)')
        finally:
            tv.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        log(f'échec : {e!r}')
        sys.exit(1)
