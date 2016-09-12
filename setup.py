from distutils.core import setup

setup(
    name='pygate_arpscanner',
    version='1.0',
    packages=['pygate_arpscanner'],
    url='www.allthingstalk.com',
    license='',
    author='Jan Bogaerts',
    author_email='jb@allthingstalk.com',
    description="plugin for the pygate: keep track of devices: are they connected to the network or not"
)


#need to execute the following linux command:
#  sudo apt-get install arp-scan