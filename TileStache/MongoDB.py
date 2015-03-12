""" Support for MongoDB databases in MBTiles format.

MBTiles (http://mbtiles.org) is a specification for storing tiled map data in
SQLite databases for immediate use and for transfer. The databases are designed for
portability of thousands, hundreds of thousands, or even millions of standard
map tile images in a single database.

This makes it easy to manage and share map tiles.

Read the spec:
    https://github.com/mapbox/mbtiles-spec/blob/master/1.1/spec.md

Example configuration:

  {
    "cache": { ... }.
    "layers":
    {
      "roads":
      {
        "provider":
        {
          "name": "mongodb",
          "tileset": "host=localhost dbname=mbtiles user=testuser password=testpassword"
        }
      }
    }
  }

MongoDB provider parameters:

  tileset:
    Required MongoDB connect string.
"""
import logging
from urlparse import urlparse, urljoin

import pymongo

from ModestMaps.Core import Coordinate


class Provider:
    """ MBTiles provider.
    
        See module documentation for explanation of constructor arguments.
    """

    def getconn(self):
        c = pymongo.mongo_client.MongoClient("mongodb://%s:%s@%s/admin" % (self.connect_options['user'], self.connect_options['password'], self.connect_options['host']))
        return c

    def __init__(self, layer, tileset):
        """
        """        
        if tileset.find("dbname") < 0:
            raise Exception('Bad scheme in MongoDB provider, must be a MongoDB connect string: "%s"' % tileset)
        
        self.connect_options = dict(option.split("=") for option in tileset.split(" "))
        self.layer = layer
        self.flip_y = True
        self.mime_type = 'image/png'

        #db = self.getconn()
        #cursor = db[self.connect_options['dbname']]
        #
        #formats = {'png': 'image/png', 'jpg': 'image/jpeg', None: None}
        #
	#try:
        #    self.mime_type = formats[cursor.metadata.find_one({"name" : "format"})["value"]]
        #except:
        #    self.mime_type = 'image/png'
        #    #raise Exception("Bad tileset '%s'" % (tileset,))
        #
        #db.close()

    @staticmethod
    def prepareKeywordArgs(config_dict):
        """ Convert configured parameters to keyword args for __init__().
        """
        return {'tileset': config_dict['tileset']}
    
    def renderTile(self, width, height, srs, coord, tile_scale):
        """ Retrieve a single tile, return a TileResponse instance.
        """
        db = self.getconn()
        cursor = db[self.connect_options['dbname']]

        tile_zoom = coord.zoom
        tile_column = coord.column
        tile_row = coord.row
        if self.flip_y:
            tile_row = (2**coord.zoom - 1) - coord.row # Hello, Paul Ramsey.

	#logging.info("SELECT tile_data FROM tiles WHERE zoom_level=%d AND tile_column=%d AND tile_row=%d AND tile_scale=%d" % (tile_zoom, tile_column, tile_row, tile_scale))

        tile_id = "%d/%d/%d/%d" % (tile_zoom, tile_column, tile_row, 1)

	content = cursor.tiles.find_one({"_id" : tile_id})
        content = content and content["d"] or None

        db.close()
        db = None

        formats = {'image/png': 'PNG', 'image/jpeg': 'JPEG', None: None}
        return TileResponse(formats[self.mime_type], content)

    def tileMetadata(self, coord):
        """ Retrieve metadata for a single tile, return a json-like object.
        """
        db = self.getconn()
        cursor = db[self.connect_options['dbname']]

        tile_row = coord.row
        if self.flip_y:
            tile_row = (2**coord.zoom - 1) - coord.row # Hello, Paul Ramsey.

        tile_id = "%d/%d/%d/%d" % (tile_zoom, tile_column, tile_row, 1)

        content = cur.tiles.find_one({"_id" : tile_id})
        content = content and content["t"] or None

        db.close()
        db = None

        return "{\"updated_at\": %d, \"zoom\": %d, \"x\": %d, \"y\": %d}" % (content, coord.zoom, coord.column, tile_row)

    def getTypeByExtension(self, extension):
        """ Get mime-type and PIL format by file extension.
        """
        if extension.lower() == 'meta':
            return 'text/plain', None

        elif extension.lower() == 'png':
            return 'image/png', 'PNG'

        elif extension.lower() == 'jpg':
            return 'image/jpeg', 'JPEG'

        else:
            raise KnownUnknown('Unknown extension in configuration: "%s"' % extension)


class TileResponse:
    """ Wrapper class for tile response that makes it behave like a PIL.Image object.
    
        TileStache.getTile() expects to be able to save one of these to a buffer.
        
        Constructor arguments:
        - format: 'PNG' or 'JPEG'.
        - content: Raw response bytes.
    """
    def __init__(self, format, content):
        self.format = format
        self.content = content
    
    def save(self, out, format):
        if self.format is not None and format != self.format:
            raise Exception('Requested format "%s" does not match tileset format "%s"' % (format, self.format))

        out.write(self.content)
