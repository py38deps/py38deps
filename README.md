# py38deps

As older versions of Python gradually end of like, many Python dependencies have raised their minimum python version requirements. `py38deps` backports their latest versions to older Python versions, aiming to support at least Python 3.8.

## Maintained dependencies

| Dep Name          | Official Repo                                                | Our Repo                                                     | Latest Version | Backport Low To | LIMITS |
| ----------------- | ------------------------------------------------------------ | ------------------------------------------------------------ | -------------- |-----------------| ------ |
| msgspec           | [msgspec/msgspec](https://github.com/msgspec/msgspec)        | [py38deps/msgspec](https://github.com/py38deps/msgspec)      | 0.21.1         | cp38~cp14       |        |
| zstandard         | [indygreg/python-zstandard](https://github.com/indygreg/python-zstandard) | [py38deps/python-zstandard](https://github.com/py38deps/python-zstandard) | 0.25.0         | cp38~cp14       |        |
| hyperframe        | [python-hyper/hyperframe](https://github.com/python-hyper/hyperframe) | [py38deps/hyperframe](https://github.com/py38deps/hyperframe) | 6.1.0          | cp38+           |        |
| hpack             | [python-hyper/hpack](https://github.com/python-hyper/hpack) | [py38deps/hpack](https://github.com/py38deps/hpack) | 4.2.0          | cp38+           |        |
| h2                | [python-hyper/h2](https://github.com/python-hyper/h2) | [py38deps/h2](https://github.com/py38deps/h2) | 4.4.1          | cp38+           |        |
| wsproto           | [python-hyper/wsproto](https://github.com/python-hyper/wsproto) | [py38deps/wsproto](https://github.com/py38deps/wsproto) | 1.3.2          | cp38+           |        |
| cffi              | [python-cffi/cffi](https://github.com/python-cffi/cffi) | [py38deps/cffi](https://github.com/py38deps/cffi) | 2.1.1          | cp38~cp15       |        |
| attrs             | [python-attrs/attrs](https://github.com/python-attrs/attrs) | [py38deps/attrs](https://github.com/py38deps/attrs) | 26.1.0         | cp38+           |        |
| idna              | [kjd/idna](https://github.com/kjd/idna) | [py38deps/idna](https://github.com/py38deps/idna) | 3.18           | cp38+           |        |
| trio              | [python-trio/trio](https://github.com/python-trio/trio) | [py38deps/trio](https://github.com/py38deps/trio) | 0.34.0         | cp38+           |        |
| PyAV              | [PyAV-Org/PyAV](https://github.com/PyAV-Org/PyAV) | [py38deps/PyAV](https://github.com/py38deps/PyAV) | 18.1.0         | cp38~cp14       |        |
| hypercorn         | [pgjones/hypercorn](https://github.com/pgjones/hypercorn) | [py38deps/hypercorn](https://github.com/py38deps/hypercorn) | 0.18.0         | cp38+           |        |
| anyio             | [agronholm/anyio](https://github.com/agronholm/anyio) | [py38deps/anyio](https://github.com/py38deps/anyio) | 4.14.2         | cp38+           | [LIMITS](doc/LIMITS-anyio.md) |
| python-multipart  | [Kludex/python-multipart](https://github.com/Kludex/python-multipart) | [py38deps/python-multipart](https://github.com/py38deps/python-multipart) | 0.0.32         | cp38+           |        |
| truststore        | [sethmlarson/truststore](https://github.com/sethmlarson/truststore) | [py38deps/truststore](https://github.com/py38deps/truststore) | 0.10.4         | cp38+           | [LIMITS](doc/LIMITS-truststore.md) |
| PyJWT             | [jpadilla/pyjwt](https://github.com/jpadilla/pyjwt) | [py38deps/pyjwt](https://github.com/py38deps/pyjwt) | 2.13.0         | cp38+           |        |
