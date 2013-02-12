""" The core class bits of TileStache.

Two important classes can be found here.

Layer represents a set of tiles in TileStache. It keeps references to
providers, projections, a Configuration instance, and other details required
for to the storage and rendering of a tile set. Layers are represented in the
configuration file as a dictionary:

    {
      "cache": ...,
      "layers":
      {
        "example-name":
        {
          "provider": { ... },
          "metatile": { ... },
          "preview": { ... },
          "projection": ...,
          "stale lock timeout": ...,
          "cache lifespan": ...,
          "write cache": ...,
          "bounds": { ... },
          "allowed origin": ...,
          "maximum cache age": ...,
          "redirects": ...,
          "tile height": ...,
          "fallback layer": ...,
          "jpeg options": ...,
          "png options": ...
        }
      }
    }

- "provider" refers to a Provider, explained in detail in TileStache.Providers.
- "metatile" optionally makes it possible for multiple individual tiles to be
  rendered at one time, for greater speed and efficiency. This is commonly used
  for the Mapnik provider. See below for more information on metatiles.
- "preview" optionally overrides the starting point for the built-in per-layer
  slippy map preview, useful for image-based layers where appropriate.
  See below for more information on the preview.
- "projection" names a geographic projection, explained in TileStache.Geography.
  If omitted, defaults to spherical mercator.
- "stale lock timeout" is an optional number of seconds to wait before forcing
  a lock that might be stuck. This is defined on a per-layer basis, rather than
  for an entire cache at one time, because you may have different expectations
  for the rendering speeds of different layer configurations. Defaults to 15.
- "cache lifespan" is an optional number of seconds that cached tiles should
  be stored. This is defined on a per-layer basis. Defaults to forever if None,
  0 or omitted.
- "write cache" is an optional boolean value to allow skipping cache write
  altogether. This is defined on a per-layer basis. Defaults to true if omitted.
- "bounds" is an optional dictionary of six tile boundaries to limit the
  rendered area: low (lowest zoom level), high (highest zoom level), north,
  west, south, and east (all in degrees).
- "allowed origin" is an optional string that shows up in the response HTTP
  header Access-Control-Allow-Origin, useful for when you need to provide
  javascript direct access to response data such as GeoJSON or pixel values.
  The header is part of a W3C working draft (http://www.w3.org/TR/cors/).
- "maximum cache age" is an optional number of seconds used to control behavior
  of downstream caches. Causes TileStache responses to include Cache-Control
  and Expires HTTP response headers. Useful when TileStache is itself hosted
  behind an HTTP cache such as Squid, Cloudfront, or Akamai.
- "redirects" is an optional dictionary of per-extension HTTP redirects,
  treated as lowercase. Useful in cases where your tile provider can support
  many formats but you want to enforce limits to save on cache usage.
  If a request is made for a tile with an extension in the dictionary keys,
  a response can be generated that redirects the client to the same tile
  with another extension.
- "tile height" gives the height of the image tile in pixels. You almost always
  want to leave this at the default value of 256, but you can use a value of 512
  to create double-size, double-resolution tiles for high-density phone screens.
- "fallback layer" is an alternate layer to use if this layer doesn't return
  a tile image.
- "jpeg options" is an optional dictionary of JPEG creation options, passed
  through to PIL: http://www.pythonware.com/library/pil/handbook/format-jpeg.htm.
- "png options" is an optional dictionary of PNG creation options, passed
  through to PIL: http://www.pythonware.com/library/pil/handbook/format-png.htm.

The public-facing URL of a single tile for this layer might look like this:

    http://example.com/tilestache.cgi/example-name/0/0/0.png

Sample JPEG creation options:

    {
      "quality": 90,
      "progressive": true,
      "optimize": true
    }

Sample PNG creation options:

    {
      "optimize": true,
      "palette": "filename.act"
    }

Sample bounds:

    {
        "low": 9, "high": 15,
        "south": 37.749, "west": -122.358,
        "north": 37.860, "east": -122.113
    }

Metatile represents a larger area to be rendered at one time. Metatiles are
represented in the configuration file as a dictionary:

    {
      "rows": 4,
      "columns": 4,
      "buffer": 64
    }

- "rows" and "columns" are the height and width of the metatile measured in
  tiles. This example metatile is four rows tall and four columns wide, so it
  will render sixteen tiles simultaneously.
- "buffer" is a buffer area around the metatile, measured in pixels. This is
  useful for providers with labels or icons, where it's necessary to draw a
  bit extra around the edges to ensure that text is not cut off. This example
  metatile has a buffer of 64 pixels, so the resulting metatile will be 1152
  pixels square: 4 rows x 256 pixels + 2 x 64 pixel buffer.

The preview can be accessed through a URL like /<layer name>/preview.html:

    {
      "lat": 33.9901,
      "lon": -116.1637,
      "zoom": 16,
      "ext": "jpg"
    }

- "lat" and "lon" are the starting latitude and longitude in degrees.
- "zoom" is the starting zoom level.
- "ext" is the filename extension, e.g. "png".
"""

import logging
from StringIO import StringIO
from urlparse import urljoin
from time import time

from Pixels import load_palette, apply_palette

try:
    from PIL import Image
except ImportError:
    import Image

from ModestMaps.Core import Coordinate

_recent_tiles = dict(hash={}, list=[])

def _addRecentTile(layer, coord, format, body, age=300):
    """ Add the body of a tile to _recent_tiles with a timeout.
    """
    key = (layer, coord, format)
    due = time() + age

    _recent_tiles['hash'][key] = body, due
    _recent_tiles['list'].append((key, due))

    logging.debug('TileStache.Core._addRecentTile() added tile to recent tiles: %s', key)

    # now look at the oldest keys and remove them if needed
    for (key, due_by) in _recent_tiles['list']:
        # new enough?
        if time() < due_by:
            break

        logging.debug('TileStache.Core._addRecentTile() removed tile from recent tiles: %s', key)

        try:
            _recent_tiles['list'].remove((key, due_by))
        except ValueError:
            pass

        try:
            del _recent_tiles['hash'][key]
        except KeyError:
            pass

def _getRecentTile(layer, coord, format):
    """ Return the body of a recent tile, or None if it's not there.
    """
    key = (layer, coord, format)
    body, use_by = _recent_tiles['hash'].get(key, (None, 0))

    # non-existent?
    if body is None:
        return None

    # new enough?
    if time() < use_by:
        logging.debug('TileStache.Core._addRecentTile() found tile in recent tiles: %s', key)
        return body

    # too old
    try:
        del _recent_tiles['hash'][key]
    except KeyError:
        pass

    return None

class Metatile:
    """ Some basic characteristics of a metatile.

        Properties:
        - rows: number of tile rows this metatile covers vertically.
        - columns: number of tile columns this metatile covers horizontally.
        - buffer: pixel width of outer edge.
    """
    def __init__(self, buffer=0, rows=1, columns=1):
        assert rows >= 1
        assert columns >= 1
        assert buffer >= 0

        self.rows = rows
        self.columns = columns
        self.buffer = buffer

    def isForReal(self):
        """ Return True if this is really a metatile with a buffer or multiple tiles.

            A default 1x1 metatile with buffer=0 is not for real.
        """
        return self.buffer > 0 or self.rows > 1 or self.columns > 1

    def firstCoord(self, coord):
        """ Return a new coordinate for the upper-left corner of a metatile.

            This is useful as a predictable way to refer to an entire metatile
            by one of its sub-tiles, currently needed to do locking correctly.
        """
        return self.allCoords(coord)[0]

    def allCoords(self, coord):
        """ Return a list of coordinates for a complete metatile.

            Results are guaranteed to be ordered left-to-right, top-to-bottom.
        """
        rows, columns = int(self.rows), int(self.columns)

        # upper-left corner of coord's metatile
        row = rows * (int(coord.row) / rows)
        column = columns * (int(coord.column) / columns)

        coords = []

        for r in range(rows):
            for c in range(columns):
                coords.append(Coordinate(row + r, column + c, coord.zoom))

        return coords

class Layer:
    """ A Layer.

        Required attributes:

          provider:
            Render provider, see Providers module.

          config:
            Configuration instance, see Config module.

          projection:
            Geographic projection, see Geography module.

          metatile:
            Some information for drawing many tiles at once.

        Optional attributes:

          stale_lock_timeout:
            Number of seconds until a cache lock is forced, default 15.

          cache_lifespan:
            Number of seconds that cached tiles should be stored, default 15.

          write_cache:
            Allow skipping cache write altogether, default true.

          bounds:
            Instance of Config.Bounds for limiting rendered tiles.

          allowed_origin:
            Value for the Access-Control-Allow-Origin HTTP response header.

          max_cache_age:
            Number of seconds that tiles from this layer may be cached by downstream clients.

          redirects:
            Dictionary of per-extension HTTP redirects, treated as lowercase.

          preview_lat:
            Starting latitude for slippy map layer preview, default 37.80.

          preview_lon:
            Starting longitude for slippy map layer preview, default -122.26.

          preview_zoom:
            Starting zoom for slippy map layer preview, default 10.

          preview_ext:
            Tile name extension for slippy map layer preview, default "png".

          tile_height:
            Height of tile in pixels, as a single integer. Tiles are generally
            assumed to be square, and Layer.render() will respond with an error
            if the rendered image is not this height.

          fallback_layer:
            A fallback layer to use, in case that this layer doesn't return a tile image.
    """
    def __init__(self, config, projection, metatile, stale_lock_timeout=15, cache_lifespan=None, write_cache=True, allowed_origin=None, max_cache_age=None, redirects=None, preview_lat=37.80, preview_lon=-122.26, preview_zoom=10, preview_ext='png', bounds=None, tile_height=256, fallback_layer=None):
        self.provider = None
        self.config = config
        self.projection = projection
        self.metatile = metatile

        self.stale_lock_timeout = stale_lock_timeout
        self.cache_lifespan = cache_lifespan
        self.write_cache = write_cache
        self.allowed_origin = allowed_origin
        self.max_cache_age = max_cache_age
        self.redirects = redirects or dict()

        self.preview_lat = preview_lat
        self.preview_lon = preview_lon
        self.preview_zoom = preview_zoom
        self.preview_ext = preview_ext

        self.bounds = bounds
        self.dim = tile_height

        self.fallback_layer = fallback_layer

        self.bitmap_palette = None
        self.jpeg_options = {}
        self.png_options = {}

    def name(self):
        """ Figure out what I'm called, return a name if there is one.

            Layer names are stored in the Configuration object, so
            config.layers must be inspected to find a matching name.
        """
        for (name, layer) in self.config.layers.items():
            if layer is self:
                return name

        return None

    def doMetatile(self):
        """ Return True if we have a real metatile and the provider is OK with it.
        """
        return self.metatile.isForReal() and hasattr(self.provider, 'renderArea')

    def render(self, coord, format):
        """ Render a tile for a coordinate, return PIL Image-like object.

            Perform metatile slicing here as well, if required, writing the
            full set of rendered tiles to cache as we go.
        """
        if self.bounds and self.bounds.excludes(coord):
            # raise NoTileLeftBehind(Image.new('L', (self.dim, self.dim), (0xFF, 0xFF, 0xFF)))
            raise NoTileLeftBehind(None)

        srs = self.projection.srs
        xmin, ymin, xmax, ymax = self.envelope(coord)
        width, height = self.dim, self.dim

        provider = self.provider
        metatile = self.metatile

        if self.doMetatile():
            # adjust render size and coverage for metatile
            xmin, ymin, xmax, ymax = self.metaEnvelope(coord)
            width, height = self.metaSize(coord)

            subtiles = self.metaSubtiles(coord)

        if self.doMetatile() or hasattr(provider, 'renderArea'):
            # draw an area, defined in projected coordinates
            tile = provider.renderArea(width, height, srs, xmin, ymin, xmax, ymax, coord.zoom)

        elif hasattr(provider, 'renderTile'):
            # draw a single tile
            width, height = self.dim, self.dim
            tile = provider.renderTile(width, height, srs, coord)

        else:
            raise KnownUnknown('Your provider lacks renderTile and renderArea methods.')

        if not hasattr(tile, 'save'):
            raise KnownUnknown('Return value of provider.renderArea() must act like an image; e.g. have a "save" method.')

        if hasattr(tile, 'size') and tile.size[1] != height:
            raise KnownUnknown('Your provider returned the wrong image size: %s instead of %d pixels tall.' % (repr(tile.size), self.dim))

        if self.bitmap_palette:
            # this is where we apply the palette if there is one

            if format.lower() == 'png':
                t_index = self.png_options.get('transparency', None)
                tile = apply_palette(tile, self.bitmap_palette, t_index)

        if self.doMetatile():
            # tile will be set again later
            tile, surtile = None, tile

            for (other, x, y) in subtiles:
                buff = StringIO()
                bbox = (x, y, x + self.dim, y + self.dim)
                subtile = surtile.crop(bbox)
                subtile.save(buff, format)
                body = buff.getvalue()

                if self.write_cache:
                    self.config.cache.save(body, self, other, format)

                if other == coord:
                    # the one that actually gets returned
                    tile = subtile

                _addRecentTile(self, other, format, body)

        return tile

    def envelope(self, coord):
        """ Projected rendering envelope (xmin, ymin, xmax, ymax) for a Coordinate.
        """
        ul = self.projection.coordinateProj(coord)
        lr = self.projection.coordinateProj(coord.down().right())

        return min(ul.x, lr.x), min(ul.y, lr.y), max(ul.x, lr.x), max(ul.y, lr.y)

    def metaEnvelope(self, coord):
        """ Projected rendering envelope (xmin, ymin, xmax, ymax) for a metatile.
        """
        # size of buffer expressed as fraction of tile size
        buffer = float(self.metatile.buffer) / self.dim

        # full set of metatile coordinates
        coords = self.metatile.allCoords(coord)

        # upper-left and lower-right expressed as fractional coordinates
        ul = coords[0].left(buffer).up(buffer)
        lr = coords[-1].right(1 + buffer).down(1 + buffer)

        # upper-left and lower-right expressed as projected coordinates
        ul = self.projection.coordinateProj(ul)
        lr = self.projection.coordinateProj(lr)

        # new render area coverage in projected coordinates
        return min(ul.x, lr.x), min(ul.y, lr.y), max(ul.x, lr.x), max(ul.y, lr.y)

    def metaSize(self, coord):
        """ Pixel width and height of full rendered image for a metatile.
        """
        # size of buffer expressed as fraction of tile size
        buffer = float(self.metatile.buffer) / self.dim

        # new master image render size
        width = int(self.dim * (buffer * 2 + self.metatile.columns))
        height = int(self.dim * (buffer * 2 + self.metatile.rows))

        return width, height

    def metaSubtiles(self, coord):
        """ List of all coords in a metatile and their x, y offsets in a parent image.
        """
        subtiles = []

        coords = self.metatile.allCoords(coord)

        for other in coords:
            r = other.row - coords[0].row
            c = other.column - coords[0].column

            x = c * self.dim + self.metatile.buffer
            y = r * self.dim + self.metatile.buffer

            subtiles.append((other, x, y))

        return subtiles

    def getTypeByExtension(self, extension):
        """ Get mime-type and PIL format by file extension.
        """
        if hasattr(self.provider, 'getTypeByExtension'):
            return self.provider.getTypeByExtension(extension)

        elif extension.lower() == 'png':
            return 'image/png', 'PNG'

        elif extension.lower() == 'jpg':
            return 'image/jpeg', 'JPEG'

        else:
            raise KnownUnknown('Unknown extension in configuration: "%s"' % extension)

    def setSaveOptionsJPEG(self, quality=None, optimize=None, progressive=None):
        """ Optional arguments are added to self.jpeg_options for pickup when saving.

            More information about options:
                http://www.pythonware.com/library/pil/handbook/format-jpeg.htm
        """
        if quality is not None:
            self.jpeg_options['quality'] = int(quality)

        if optimize is not None:
            self.jpeg_options['optimize'] = bool(optimize)

        if progressive is not None:
            self.jpeg_options['progressive'] = bool(progressive)

    def setSaveOptionsPNG(self, optimize=None, palette=None):
        """ Optional arguments are added to self.png_options for pickup when saving.

            Palette argument is a URL relative to the configuration file,
            and it implies bits and optional transparency options.

            More information about options:
                http://www.pythonware.com/library/pil/handbook/format-png.htm
        """
        if optimize is not None:
            self.png_options['optimize'] = bool(optimize)

        if palette is not None:
            palette = urljoin(self.config.dirpath, palette)
            palette, bits, t_index = load_palette(palette)

            self.bitmap_palette, self.png_options['bits'] = palette, bits

            if t_index is not None:
                self.png_options['transparency'] = t_index

class KnownUnknown(Exception):
    """ There are known unknowns. That is to say, there are things that we now know we don't know.

        This exception gets thrown in a couple places where common mistakes are made.
    """
    pass

class NoTileLeftBehind(Exception):
    """ Leave no tile in the cache.

        This exception can be thrown in a provider to signal to
        TileStache.getTile() that the result tile should be returned,
        but not saved in a cache. Useful in cases where a full tileset
        is being rendered for static hosting, and you don't want millions
        of identical ocean tiles.

        The one constructor argument is an instance of PIL.Image or
        some other object with a save() method, as would be returned
        by provider renderArea() or renderTile() methods.
    """
    def __init__(self, tile):
        self.tile = tile
        Exception.__init__(self, tile)

class TheTileIsInAnotherCastle(Exception):
    """ Ask a client to look someplace else for a tile.

        This exception can be thrown in a provider to signal
        to HTTP clients that a tile should be asked-for elsewhere.
    """
    def __init__(self, path_info):
        self.path_info = path_info
        Exception.__init__(self, path_info)

def _preview(layer):
    """ Get an HTML response for a given named layer.
    """
    layername = layer.name()
    lat, lon = layer.preview_lat, layer.preview_lon
    zoom = layer.preview_zoom
    ext = layer.preview_ext

    return """<!DOCTYPE html>
<html>
<head>
    <title>TileStache Preview: %(layername)s</title>
    <script src="http://code.modestmaps.com/tilestache/modestmaps.min.js" type="text/javascript"></script>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=0;">
    <style type="text/css">
        html, body, #map {
            position: absolute;
            width: 100%%;
            height: 100%%;
            margin: 0;
            padding: 0;
        }
    </style>
</head>
<body>
    <div id="map"></div>
    <script type="text/javascript" defer>
    <!--
        var template = '{Z}/{X}/{Y}.%(ext)s';
        var provider = new com.modestmaps.TemplatedMapProvider(template);
        var map = new MM.Map('map', provider, null, [
            new MM.TouchHandler(),
            new MM.DragHandler(),
            new MM.DoubleClickHandler()
        ]);
        map.setCenterZoom(new com.modestmaps.Location(%(lat).6f, %(lon).6f), %(zoom)d);
        // hashify it
        new MM.Hash(map);
    //-->
    </script>
</body>
</html>
""" % locals()
