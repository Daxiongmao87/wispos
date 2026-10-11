"""The WispOS mark, built from geometry. Run: python3 -I build_mark.py   (writes wispos-mark.svg/.png-ready SVGs)

An orb (radius R = 1) with a thick concentric half-ring cradling it from below. The ring is cut into three arcs
and two gaps of 36 degrees each, so every gap is exactly as long as every arc, at every radius (radial cuts).
Ring thickness 0.64 R and orb-to-ring gap 0.33 R. Colours: white orb with blue arcs on black (the dark ground the
boot screen uses), and a one-colour version. Clear space around the mark: 0.45 R. Smallest use: 24 px, below which
the orb alone is the icon. The idea (a cradling ring, cut like a dotted line for haze) was the operator's."""
import math
GAP, T = 0.33, 0.64
RIN = 1 + GAP; ROUT = RIN + T
SEG = 180 / 5                                   # three arcs + two gaps = five equal 36-degree parts of the half ring
BG, WHITE, BLUE, PALE = '#000000', '#ffffff', '#8ec5ff', '#c2e0fb'

def arc(a0, a1, n=240):
    outer = [(ROUT * math.cos(math.radians(a0 + (a1 - a0) * i / n)), ROUT * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]
    inner = [(RIN * math.cos(math.radians(a1 - (a1 - a0) * i / n)), RIN * math.sin(math.radians(a1 - (a1 - a0) * i / n))) for i in range(n + 1)]
    return outer + inner

def svg(orb, ring, size=480, margin=0.45, ground=BG):
    xs = (-ROUT - margin, ROUT + margin); ys = (-ROUT - margin, 1 + margin)
    S = size / (xs[1] - xs[0]); H = (ys[1] - ys[0]) * S
    px = lambda x, y: ((x - xs[0]) * S, (ys[1] - y) * S)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {H:.0f}" width="{size}" height="{H:.0f}">']
    if ground: out.append(f'<rect width="{size}" height="{H:.0f}" fill="{ground}"/>')
    for k in range(3):
        a0 = 180 + k * 2 * SEG
        out.append('<path d="M ' + ' L '.join('%.2f,%.2f' % px(*p) for p in arc(a0, a0 + SEG)) + f' Z" fill="{ring}"/>')
    cx, cy = px(0, 0); out.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{S:.2f}" fill="{orb}"/>')
    return '\n'.join(out) + '\n</svg>\n'

if __name__ == '__main__':
    open('wispos-mark.svg', 'w').write(svg(WHITE, BLUE))
    open('wispos-mark-mono.svg', 'w').write(svg(PALE, PALE))
    open('wispos-mark-transparent.svg', 'w').write(svg(WHITE, BLUE, ground=None))
