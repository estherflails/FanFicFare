#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright (c) 2012, Jean-Rémy Bancel <jean-remy.bancel@telecom-paristech.org>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of the Chromagon Project nor the
#       names of its contributors may be used to endorse or promote products
#       derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL Jean-Rémy Bancel BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""
Parse the Chrome Cache File
See http://www.chromium.org/developers/design-documents/network-stack/disk-cache
for design details
"""

from __future__ import absolute_import
from __future__ import print_function
import gzip
import os
import struct
import sys
import re
import time

def do_cprofile(func):
    def profiled_func(*args, **kwargs):
        t=0
        try:
            t = time.time()
            result = func(*args, **kwargs)
            t = time.time() - t
            return result
        finally:
            print("time:%s"%t)
    return profiled_func

try:
    from brotli import decompress
    @do_cprofile
    def brotli_decompress(inbuf):
        return decompress(inbuf)
except:
    # Calibre doesn't include brotli, so use packaged brotlipython
    # which is waaaay slower, but pure python.
    from brotlipython import brotlidec
    @do_cprofile
    def brotli_decompress(inbuf):
        # wants the output, too, but returns it
        return brotlidec(inbuf,[])
import time

from . import csvOutput
from . import SuperFastHash

from .cacheAddress import CacheAddress
from .cacheBlock import CacheBlock
from .cacheData import CacheData
from .cacheEntry import CacheEntry
from six.moves import range


def parse(path, urls=None):
    """
    Reads the whole cache and store the collected data in a table
    or find out if the given list of urls is in the cache. If yes it
    return a list of the corresponding entries.
    """
    # Verifying that the path end with / (What happen on windows?)
    path = os.path.abspath(path)
    cacheBlock = CacheBlock(os.path.join(path, "index"))

    # Checking type
    if cacheBlock.type != CacheBlock.INDEX:
        raise Exception("Invalid Index File")

    index = open(os.path.join(path, "index"), 'rb')

    # Skipping Header
    index.seek(92*4)

    cache = []
    # If no url is specified, parse the whole cache
    if urls == None:
        for key in range(cacheBlock.tableSize):
            raw = struct.unpack('I', index.read(4))[0]
            if raw != 0:
                entry = CacheEntry(CacheAddress(raw, path=path))
                # Checking if there is a next item in the bucket because
                # such entries are not stored in the Index File so they will
                # be ignored during iterative lookup in the hash table
                while entry.next != 0:
                    cache.append(entry)
                    entry = CacheEntry(CacheAddress(entry.next, path=path))
                cache.append(entry)
    else:
        # Find the entry for each url
        for url in urls:
            url = bytes(url,'utf8')
            # Compute the key and seeking to it
            hash = SuperFastHash.superFastHash(url)
            key = hash & (cacheBlock.tableSize - 1)
            index.seek(92*4 + key*4)

            addr = struct.unpack('I', index.read(4))[0]
            # Checking if the address is initialized (i.e. used)
            if addr & 0x80000000 == 0:
                print("%s is not in the cache" % url, file=sys.stderr)

            # Follow the chained list in the bucket
            else:
                entry = CacheEntry(CacheAddress(addr, path=path))
                while entry.hash != hash and entry.next != 0:
                    entry = CacheEntry(CacheAddress(entry.next, path=path))
                if entry.hash == hash:
                    cache.append(entry)
    index.close()
    return cache

class ChromeCache(object):
    def __init__(self,path):
        self.cache = parse(path)
        self.hash_cache = {}
        # t = time.time()
        for entry in self.cache:
            key = entry.keyToStr()
            if 'fanfiction.net' not in key:
                continue
            self.hash_cache[key] = entry
            normkey = re.sub(r'^(https://www.fanfiction.net/s/\d+/\d+/).+$',r'\1',key)
            ## either overwrites (no harm), or adds new.
            self.hash_cache[normkey] = entry
        # print("======:%s"%(time.time()-t))

    def get_cached_file(self,url):
        if url in self.hash_cache:
            entry = self.hash_cache[url]
            for i in range(len(entry.data)):
                if entry.data[i].type == CacheData.UNKNOWN:
                    # Extracting data into a file
                    data = entry.data[i].data()

                    # print("content-encoding:%s"%entry.httpHeader.headers.get(b'content-encoding',''))
                    if entry.httpHeader != None and \
                       b'content-encoding' in entry.httpHeader.headers:
                        if entry.httpHeader.headers[b'content-encoding'] == b"gzip":
                            data = gzip.decompress(data)
                        elif entry.httpHeader.headers[b'content-encoding'] == b"br":
                            data = brotli_decompress(data)
                    return data
        return None

def exportToHTML(cache, outpath):
    """
    Export the cache in html
    """

    # Checking that the directory exists and is writable
    if not os.path.exists(outpath):
        os.makedirs(outpath)
    outpath = os.path.abspath(outpath)

    index = open(os.path.join(outpath,"index.html"), 'w')
    index.write("<UL>")

    for entry in cache:
        # Adding a link in the index
        if entry.keyLength > 100:
            entry_name = entry.keyToStr()[:100] + "..."
        else:
            entry_name = entry.keyToStr()
        index.write('<LI><a href="%08x.html">%s</a></LI>'%(entry.hash, entry_name))
        # We handle the special case where entry_name ends with a slash
        page_basename = entry_name.split('/')[-2] if entry_name.endswith('/') else entry_name.split('/')[-1]

        # Creating the entry page
        page = open(os.path.join(outpath,"%08x.html"%entry.hash), 'w')
        page.write("""<!DOCTYPE html>
                      <html lang="en">
                      <head>
                      <meta charset="utf-8">
                      </head>
                      <body>""")

        # Details of the entry
        page.write("<b>Hash</b>: 0x%08x<br />"%entry.hash)
        page.write("<b>Usage Counter</b>: %d<br />"%entry.usageCounter)
        page.write("<b>Reuse Counter</b>: %d<br />"%entry.reuseCounter)
        page.write("<b>Creation Time</b>: %s<br />"%entry.creationTime)
        page.write("<b>Key</b>: %s<br>"%entry.keyToStr())
        page.write("<b>State</b>: %s<br>"%CacheEntry.STATE[entry.state])

        page.write("<hr>")
        ## entry.data normally 2 or 1
        ## 2 for headers and data, 1 for headers only.
        if len(entry.data) == 0:
            page.write("No data associated with this entry :-(")
        for i in range(len(entry.data)):
            if entry.data[i].type == CacheData.UNKNOWN:
                # Extracting data into a file
                name = hex(entry.hash) + "_" + str(i)
                entry.data[i].save(os.path.join(outpath,name))

                # print("content-encoding:%s"%entry.httpHeader.headers.get(b'content-encoding',''))
                if entry.httpHeader != None and \
                   b'content-encoding' in entry.httpHeader.headers:
                    if entry.httpHeader.headers[b'content-encoding'] == b"gzip":
                        # XXX Highly inefficient !!!!!
                        try:
                            input = gzip.open(os.path.join(outpath, name), 'rb')
                            output = open(os.path.join(outpath, name + "u"), 'wb')
                            output.write(input.read())
                            input.close()
                            output.close()
                            page.write('<a href="%su">%s</a>'%(name, page_basename))
                            # print("gunzip'ed:%s"%name)
                        except IOError:
                            page.write("Something wrong happened while unzipping")
                    elif entry.httpHeader.headers[b'content-encoding'] == b"br":
                        try:
                            with open(os.path.join(outpath,name), 'rb') as input:
                                with open(os.path.join(outpath,name + "u"), 'wb') as output:
                                    output.write(brotli.decompress(input.read()))
                            page.write('<a href="%su">%s</a>'%(name, page_basename))
                            # print("unbrotli'ed:%s"%name)
                        except IOError:
                            page.write("Something wrong happened while unzipping")
                else:
                    page.write('<a href="%s">%s</a>'%(name ,
                               entry.keyToStr().split('/')[-1]))


                # If it is a picture, display it
                if entry.httpHeader != None:
                    if b'content-type' in entry.httpHeader.headers and\
                       b"image" in entry.httpHeader.headers[b'content-type']:
                        page.write('<br /><img src="%s">'%(name))
            # HTTP Header
            else:
                page.write("<u>HTTP Header</u><br />")
                for key, value in entry.data[i].headers.items():
                    page.write("<b>%s</b>: %s<br />"%(key, value))
            page.write("<hr>")
        page.write("</body></html>")
        page.close()

    index.write("</UL>")
    index.close()

def exportTol2t(cache):
    """
    Export the cache in CSV log2timeline compliant format
    """

    output = []
    output.append(["date",
                   "time",
                   "timezone",
                   "MACB",
                   "source",
                   "sourcetype",
                   "type",
                   "user",
                   "host",
                   "short",
                   "desc",
                   "version",
                   "filename",
                   "inode",
                   "notes",
                   "format",
                   "extra"])

    for entry in cache:
        date = entry.creationTime.date().strftime("%m/%d/%Y")
        time = entry.creationTime.time()
        # TODO get timezone
        timezone = 0
        short = entry.keyToStr()
        descr = "Hash: 0x%08x" % entry.hash
        descr += " Usage Counter: %d" % entry.usageCounter
        if entry.httpHeader != None:
            if 'content-type' in entry.httpHeader.headers:
                descr += " MIME: %s" % entry.httpHeader.headers['content-type']

        output.append([date,
                       time,
                       timezone,
                       "MACB",
                       "WEBCACHE",
                       "Chrome Cache",
                       "Cache Entry",
                       "-",
                       "-",
                       short,
                       descr,
                       "2",
                       "-",
                       "-",
                       "-",
                       "-",
                       "-",
                       ])

    csvOutput.csvOutput(output)
