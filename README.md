# py38deps

As older versions of Python gradually end of like, many Python dependencies have raised their minimum python version requirements. `py38deps` backports their latest versions to older Python versions, aiming to support at least Python 3.8.

## Maintained dependencies

| Dep Name          | Official Repo                                                | Our Repo                                                     | Latest Version | Backport Low To |
| ----------------- | ------------------------------------------------------------ | ------------------------------------------------------------ | -------------- |-----------------|
| msgspec           | [msgspec/msgspec](https://github.com/msgspec/msgspec)        | [LmeSzinc/msgspec](https://github.com/LmeSzinc/msgspec)      | 0.21.1         | cp38~cp14       |
| zstandard         | [indygreg/python-zstandard](https://github.com/indygreg/python-zstandard) | [LmeSzinc/python-zstandard](https://github.com/LmeSzinc/python-zstandard) | 0.25.0         | cp38~cp14       |
| hyperframe        | [python-hyper/hyperframe](https://github.com/python-hyper/hyperframe) | [LmeSzinc/hyperframe](https://github.com/LmeSzinc/hyperframe) | 6.1.0          | cp38+           |
| hpack             | [python-hyper/hpack](https://github.com/python-hyper/hpack) | [LmeSzinc/hpack](https://github.com/LmeSzinc/hpack) | 4.2.0          | cp38+           |
| h2                | [python-hyper/h2](https://github.com/python-hyper/h2) | [LmeSzinc/h2](https://github.com/LmeSzinc/h2) | 4.4.1          | cp38+           |
| wsproto           | [python-hyper/wsproto](https://github.com/python-hyper/wsproto) | [LmeSzinc/wsproto](https://github.com/LmeSzinc/wsproto) | 1.3.2          | cp38+           |
| cffi              | [python-cffi/cffi](https://github.com/python-cffi/cffi) | [LmeSzinc/cffi](https://github.com/LmeSzinc/cffi) | 2.1.1          | cp38~cp15       |
| attrs             | [python-attrs/attrs](https://github.com/python-attrs/attrs) | [LmeSzinc/attrs](https://github.com/LmeSzinc/attrs) | 26.1.0         | cp38+           |
| idna              | [kjd/idna](https://github.com/kjd/idna) | [LmeSzinc/idna](https://github.com/LmeSzinc/idna) | 3.18           | cp38+           |
| trio              | [python-trio/trio](https://github.com/python-trio/trio) | [LmeSzinc/trio](https://github.com/LmeSzinc/trio) | 0.34.0         | cp38+           |
| PyAV              | [PyAV-Org/PyAV](https://github.com/PyAV-Org/PyAV) | [LmeSzinc/PyAV](https://github.com/LmeSzinc/PyAV) | 18.1.0         | cp38~cp14       |
| hypercorn         | [pgjones/hypercorn](https://github.com/pgjones/hypercorn) | [LmeSzinc/hypercorn](https://github.com/LmeSzinc/hypercorn) | 0.18.0         | cp38+           |
| anyio             | [agronholm/anyio](https://github.com/agronholm/anyio) | [LmeSzinc/anyio](https://github.com/LmeSzinc/anyio) | 4.14.2         | cp38+           |
| python-multipart  | [Kludex/python-multipart](https://github.com/Kludex/python-multipart) | [LmeSzinc/python-multipart](https://github.com/LmeSzinc/python-multipart) | 0.0.32         | cp38+           |
| truststore        | [sethmlarson/truststore](https://github.com/sethmlarson/truststore) | [LmeSzinc/truststore](https://github.com/LmeSzinc/truststore) | 0.10.4         | cp38+           |
