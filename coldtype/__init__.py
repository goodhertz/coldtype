name = "coldtype"

import math
from collections import OrderedDict
import freetype
from freetype.raw import *
from fontTools.misc.transform import Transform
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.recordingPen import RecordingPen, replayRecording
from fontTools.pens.boundsPen import ControlBoundsPen, BoundsPen
from fontTools.misc.bezierTools import calcCubicArcLength, splitCubicAtT
from fontTools.ttLib import TTFont
from furniture.geometry import Rect
import uharfbuzz as hb
import unicodedata

try:
    from booleanOperations.booleanGlyph import BooleanGlyph
    import drawBot as db
except:
    db = None


class HarfbuzzFrame():
    def __init__(self, info, position, frame):
        self.gid = info.codepoint
        self.info = info
        self.position = position
        self.frame = frame

    def __repr__(self):
        return f"HarfbuzzFrame: gid{self.gid}@{self.frame}"


class Harfbuzz():
    def __init__(self, font_path):
        with open(font_path, 'rb') as fontfile:
            self.fontdata = fontfile.read()
        self.fontPath = font_path
        
        #self.font = hb.Font(self.face)
        #self.upem = int(self.face.upem)
        face = hb.Face(self.fontdata)
        self.upem = int(face.upem)
        #self.upem = self.face.upem
        #self.font.scale = (self.upem, self.upem)
        #hb.ot_font_set_funcs(self.font)

    def getFrames(self, text="", axes=dict(), features=dict(kern=True, liga=True), height=1000):
        face = hb.Face(self.fontdata)
        font = hb.Font(face)
        font.scale = (face.upem, face.upem)
        hb.ot_font_set_funcs(font)
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        
        if len(axes.items()) > 0:
            font.set_variations(axes)
        
        hb.shape(font, buf, features)
        infos = buf.glyph_infos
        positions = buf.glyph_positions
        frames = []
        x = 0
        for info, pos in zip(infos, positions):
            gid = info.codepoint
            cluster = info.cluster
            x_advance = pos.x_advance
            x_offset = pos.x_offset
            y_offset = pos.y_offset
            frames.append(HarfbuzzFrame(info, pos, Rect((x+x_offset, y_offset, x_advance, height)))) # 100?
            x += x_advance
        return frames


class FreetypeReader():
    def __init__(self, font_path, ttfont):
        self.fontPath = font_path
        self.font = freetype.Face(font_path)
        #self.font.set_char_size(1000)
        #self.scale = scale
        self.ttfont = ttfont
        try:
            self.axesOrder = [a.axisTag for a in self.ttfont['fvar'].axes]
        except:
            self.axesOrder = []
    
    def setVariations(self, axes=dict()):
        if len(self.axesOrder) > 0:
            coords = []
            for name in self.axesOrder:
                coord = FT_Fixed(int(axes[name]) << 16)
                coords.append(coord)
            ft_coords = (FT_Fixed * len(coords))(*coords)
            freetype.FT_Set_Var_Design_Coordinates(self.font._FT_Face, len(ft_coords), ft_coords)
    
    def setGlyph(self, glyph_id):
        self.glyph_id = glyph_id
        # FT_LOAD_NO_SCALE
        flags = freetype.FT_LOAD_DEFAULT | freetype.FT_LOAD_NO_SCALE #| freetype.FT_LOAD_NO_HINTING | freetype.FT_LOAD_NO_BITMAP
        if isinstance(glyph_id, int):
            self.font.load_glyph(glyph_id, flags)
        else:
            self.font.load_char(glyph_id, flags)

    def drawOutlineToPen(self, pen, raiseCubics=True):
        outline = self.font.glyph.outline
        rp = RecordingPen()
        self.font.glyph.outline.decompose(rp, move_to=FreetypeReader.moveTo, line_to=FreetypeReader.lineTo, conic_to=FreetypeReader.conicTo, cubic_to=FreetypeReader.cubicTo)
        if len(rp.value) > 0:
            rp.closePath()
        replayRecording(rp.value, pen)
        return
    
    def drawTTOutlineToPen(self, pen):
        glyph_name = self.ttfont.getGlyphName(self.glyph_id)
        g = self.ttfont.getGlyphSet()[glyph_name]
        try:
            g.draw(pen)
        except TypeError:
            print("could not draw TT outline")
            
    def moveTo(a, pen):
        if len(pen.value) > 0:
            pen.closePath()
        pen.moveTo((a.x, a.y))

    def lineTo(a, pen):
        pen.lineTo((a.x, a.y))

    def conicTo(a, b, pen):
        if False:
            pen.qCurveTo((a.x, a.y), (b.x, b.y))
        else:
            c0 = pen.value[-1][-1][-1]
            c1 = (c0[0] + (2/3)*(a.x - c0[0]), c0[1] + (2/3)*(a.y - c0[1]))
            c2 = (b.x + (2/3)*(a.x - b.x), b.y + (2/3)*(a.y - b.y))
            c3 = (b.x, b.y)
            pen.curveTo(c1, c2, c3)
            #pen.lineTo(c3)

    def cubicTo(a, b, c, pen):
        pen.curveTo((a.x, a.y), (b.x, b.y), (c.x, c.y))


class CurveCutter():
    def __init__(self, g, inc=0.0015):
        self.pen = RecordingPen()
        g.draw(self.pen)
        self.inc = inc
        self.length = self.calcCurveLength()
    
    def calcCurveLength(self):
        length = 0
        for i, (t, pts) in enumerate(self.pen.value):
            if t == "curveTo":
                p1, p2, p3 = pts
                p0 = self.pen.value[i-1][-1][-1]
                length += calcCubicArcLength(p0, p1, p2, p3)
            elif t == "lineTo":
                pass # todo
        return length

    def subsegment(self, start=None, end=None):
        inc = self.inc
        length = self.length
        ended = False
        _length = 0
        out = []
        for i, (t, pts) in enumerate(self.pen.value):
            if t == "curveTo":
                p1, p2, p3 = pts
                p0 = self.pen.value[i-1][-1][-1]
                length_arc = calcCubicArcLength(p0, p1, p2, p3)
                if _length + length_arc < end:
                    _length += length_arc
                else:
                    t = inc
                    tries = 0
                    while not ended:
                        a, b = splitCubicAtT(p0, p1, p2, p3, t)
                        length_a = calcCubicArcLength(*a)
                        if _length + length_a > end:
                            ended = True
                            out.append(("curveTo", a[1:]))
                        else:
                            t += inc
                            tries += 1
            if t == "lineTo":
                pass # TODO
            if not ended:
                out.append((t, pts))
    
        if out[-1][0] != "endPath":
            out.append(("endPath",[]))
        return out

    def subsegmentPoint(self, start=0, end=1):
        inc = self.inc
        subsegment = self.subsegment(start=start, end=end)
        try:
            t, (a, b, c) = subsegment[-2]
            tangent = math.degrees(math.atan2(c[1] - b[1], c[0] - b[0]) + math.pi*.5)
            return c, tangent
        except ValueError:
            return None, None

class StyledString():
    def __init__(self,
            text="",
            fontFile=None,
            fontSize=12,
            tracking=0,
            trackingLimit=0,
            space=None,
            variations=dict(),
            variationLimits=dict(),
            increments=dict(),
            features=dict(),
            align="C",
            rect=None):
        self.text = text
        self.fontFile = os.path.expanduser(fontFile)
        self.ttfont = TTFont(self.fontFile)
        self.harfbuzz = Harfbuzz(self.fontFile)
        self.upem = self.harfbuzz.upem
        #try:
        #    self.upem = self.ttfont["head"].unitsPerEm
        #except:
        #self.upem = 1000
        
        self.fontSize = fontSize
        self.tracking = tracking
        self.trackingLimit = trackingLimit
        self.features = {**dict(kern=True, liga=True), **features}
        self.path = None
        self.offset = (0, 0)
        self.rect = None
        self.increments = increments
        self.space = space
        self.align = align
        self.rect = rect
        self.axes = OrderedDict()
        self.variations = dict()
        self.variationLimits = dict()
        try:
            fvar = self.ttfont['fvar']
        except KeyError:
            fvar = None
        if fvar:
            for axis in fvar.axes:
                self.axes[axis.axisTag] = axis
                self.variations[axis.axisTag] = axis.defaultValue
                self.variationLimits[axis.axisTag] = axis.minValue
        self.addVariations(variations)
        
        os2 = self.ttfont["OS/2"]
        if hasattr(os2, "sCapHeight"):
            self.ch = os2.sCapHeight
        else:
            self.ch = 1000 # alternative?
        
        if rect:
            self.place(self.rect)
    
    def addVariations(self, variations, limits=dict()):
        for k, v in self.normalizeVariations(variations).items():
            self.variations[k] = v
        for k, v in self.normalizeVariations(limits).items():
            self.variationLimits[k] = v
    
    def normalizeVariations(self, variations):
        if variations.get("scale") != None:
            scale = variations["scale"]
            del variations["scale"]
        else:
            scale = False
        for k, v in variations.items():
            try:
                axis = self.axes[k]
            except KeyError:
                print("Invalid axis", self.fontFile, k)
                continue
                #raise Exception("Invalid axis", self.fontFile, k)
            if v == "min":
                variations[k] = axis.minValue
            elif v == "max":
                variations[k] = axis.maxValue
            elif v == "default":
                variations[k] = axis.defaultValue
            elif scale and v <= 1.0:
                variations[k] = int((axis.maxValue-axis.minValue)*v + axis.minValue)
            else:
                if v < axis.minValue or v > axis.maxValue:
                    variations[k] = max(axis.minValue, min(axis.maxValue, v))
                    print("----------------------")
                    print("Invalid Font Variation")
                    print(self.fontFile, self.axes[k].axisTag, v)
                    print("> setting", v, "<to>", variations[k])
                    print("----------------------")
                else:
                    variations[k] = v
        return variations
    
    def vowelMark(self, u):
        return "KASRA" in u or "FATHA" in u or "DAMMA" in u or "TATWEEL" in u or "SUKUN" in u
    
    # look away
    def trackFrames(self, frames, glyph_names):
        t = self.tracking*1/self.scale()
        t = self.tracking
        x_off = 0
        has_kashida = False
        try:
            self.ttfont.getGlyphID("uni0640")
            has_kashida = True
        except KeyError:
            has_kashida = False
        
        if not has_kashida:
            for idx, f in enumerate(frames):
                gn = glyph_names[idx]
                f.frame = f.frame.offset(x_off, 0)
                x_off += t
                if self.space and gn == "space":
                    x_off += self.space
        else:
            for idx, frame in enumerate(frames):
                frame.frame = frame.frame.offset(x_off, 0)
                try:
                    u = glyph_names[idx]
                    if self.vowelMark(u):
                        continue
                    u_1 = glyph_names[idx+1]
                    if self.vowelMark(u_1):
                        u_1 = glyph_names[idx+2]
                    if "MEDIAL" in u_1 or "INITIAL" in u_1:
                        f = 1.6
                        if "MEEM" in u_1 or "LAM" in u_1:
                            f = 2.7
                        x_off += t*f
                except IndexError:
                    pass
        return frames
    
    def adjustFramesForPath(self, frames):
        self.tangents = []
        self.limit = len(frames)
        if self.path:
            for idx, f in enumerate(frames):
                ow = f.frame.x+f.frame.w/2
                if ow > self.cutter.length:
                    self.limit = min(idx, self.limit)
                else:
                    p, t = self.cutter.subsegmentPoint(end=ow)
                    f.frame.x = p[0]
                    f.frame.y = f.frame.y + p[1]
                    self.tangents.append(t)
        return frames[0:self.limit]
    
    def getGlyphFrames(self):
        frames = self.harfbuzz.getFrames(text=self.text, axes=self.variations, features=self.features, height=self.ch)
        glyph_names = []
        for f in frames:
            f.frame = f.frame.scale(self.scale())
            glyph_name = self.ttfont.getGlyphName(f.gid)
            code = glyph_name.replace("uni", "")
            #print(glyph_name, f.gid)
            try:
                glyph_names.append(unicodedata.name(chr(int(code, 16))))
            except:
                glyph_names.append(code)
        if ".notdef" in glyph_names:
            pass
            #print("NOTDEF found")
        return self.adjustFramesForPath(self.trackFrames(frames, glyph_names))
    
    def width(self): # size?
        return self.getGlyphFrames()[-1].frame.point("SE").x
    
    def scale(self):
        return self.fontSize / self.upem
    
    def fit(self, width):
        _vars = self.variations
        current_width = self.width()
        self.tries = 0
        if current_width > width: # need to shrink
            while self.tries < 1000 and current_width > width:
                if self.tracking > self.trackingLimit:
                    self.tracking -= self.increments.get("tracking", 0.25)
                else:
                    for k, v in self.variationLimits.items():
                        if self.variations[k] > self.variationLimits[k]:
                            self.variations[k] -= self.increments.get(k, 1)
                            break
                self.tries += 1
                current_width = self.width()
        elif current_width < width: # need to expand
            pass
        else:
            return
        
        if current_width > width:
            print("DOES NOT FIT", self.tries, self.text)
    
    def place(self, rect):
        self.rect = rect
        self.fit(rect.w)
        x = rect.w/2 - self.width()/2
        self.offset = (0, 0)
        #self.offset = rect.offset(0, rect.h/2 - ch/2).xy()
    
    def addPath(self, path):
        self.path = path
        self.cutter = CurveCutter(path)
    
    def formattedString(self):
        if db:
            feas = dict(self.features)
            del feas["kern"]
            return db.FormattedString(self.text, font=self.fontFile, fontSize=self.fontSize, lineHeight=self.fontSize+2, tracking=self.tracking, fontVariations=self.variations, openTypeFeatures=feas)
        else:
            print("No DrawBot available")
            return None
    
    def drawToPen(self, out_pen, useTTFont=False):
        fr = FreetypeReader(self.fontFile, ttfont=self.ttfont)
        fr.setVariations(self.variations)
        # self.harfbuzz.setFeatures ???
        self._frames = self.getGlyphFrames()
        
        for idx, frame in enumerate(self._frames):
            fr.setGlyph(frame.gid)
            s = self.scale()
            t = Transform()
            t = t.scale(s)
            t = t.translate(frame.frame.x/self.scale(), frame.frame.y/self.scale())
            if self.path:
                tangent = self.tangents[idx]
                t = t.rotate(math.radians(tangent-90))
                t = t.translate(-frame.frame.w*0.5/self.scale())
            #print(self.offset)
            t = t.translate(self.offset[0]/self.scale(), self.offset[1]/self.scale())
            tp = TransformPen(out_pen, (t[0], t[1], t[2], t[3], t[4], t[5]))
            if useTTFont:
                fr.drawTTOutlineToPen(tp)
            else:
                fr.drawOutlineToPen(tp, raiseCubics=True)
            
    def asRecording(self):
        rp = RecordingPen()
        self.drawToPen(rp)

        if self.rect:
            cbp = ControlBoundsPen(None)
            replayRecording(rp.value, cbp)
            mnx, mny, mxx, mxy = cbp.bounds
            os2 = self.ttfont["OS/2"]
            ch = self.ch * self.scale()
            #if hasattr(os2, "sCapHeight"):
            #    ch = os2.sCapHeight * self.scale()
            #else:
            #    ch = mxy - mny
            y = self.align[0]
            x = self.align[1] if len(self.align) > 1 else "C"
            w = mxx-mnx

            if x == "C":
                xoff = -mnx + self.rect.x + self.rect.w/2 - w/2
            elif x == "W":
                xoff = self.rect.x
            elif x == "E":
                xoff = -mnx + self.rect.x + self.rect.w - w
            
            if y == "C":
                yoff = self.rect.y + self.rect.h/2 - ch/2
            elif y == "N":
                yoff = self.rect.y + self.rect.h - ch
            elif y == "S":
                yoff = self.rect.y
            if False:
                with savedState():
                    fill(1, 0, 0.5, 0.5)
                    bp = BezierPath()
                    bp.rect(mnx, mny, mxx-mnx, mxy-mny)
                    bp.translate(xoff, yoff)
                    drawPath(bp)
            diff = self.rect.w - (mxx-mnx)
            rp2 = RecordingPen()
            tp = TransformPen(rp2, (1, 0, 0, 1, xoff, yoff))
            replayRecording(rp.value, tp)
            self._final_offset = (xoff, yoff)
            return rp2
        else:
            return rp

    def asGlyph(self, removeOverlap=False):
        recording = self.asRecording()
        bg = BooleanGlyph()
        replayRecording(recording.value, bg.getPen())
        if removeOverlap:
            bg = bg.removeOverlap()
        return bg
    
    def drawBotDraw(self, removeOverlap=False):
        if db:
            g = self.asGlyph(removeOverlap=removeOverlap)
            bp = db.BezierPath()
            g.draw(bp)
            db.drawPath(bp)
        else:
            print("No DrawBot available")


def flipped_svg_pen(recording, height):
    svg_pen = SVGPathPen(None)
    flipped = []
    for t, pts in recording.value:
        flipped.append((t, [(round(x, 1), round(height-y, 1)) for x, y in pts]))
    replayRecording(flipped, svg_pen)
    return svg_pen