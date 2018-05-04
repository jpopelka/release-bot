#!/usr/bin/bash -e

HERE=$(dirname $0)

patch -p2 --directory "/usr/lib/python2.7/site-packages/pyrpkg/" < "${HERE}/rpkg-getuser.patch"

exit 0
