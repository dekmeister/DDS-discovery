# The one place the domain ID is written down. Every process and every run.sh
# reads it from here.
#
# Not 0: domain 0 is the default for anything that does not choose, so a stray
# ROS 2 node or someone else's test rig on this segment would join our labs and
# show up as unexplained discovery traffic.
DOMAIN_ID = 42
