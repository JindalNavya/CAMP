# Data

`sample_stage-dal09.json` is a 7-record excerpt (real, anonymized log lines) from the
IBM/Virginia Tech Docker registry trace, included only so the code can be smoke-tested
and the log format is visible. Its 4 usable events are far too few for meaningful results.

The full trace (about 22 GB) is **not** included. Download it from
<https://dssl.cs.vt.edu/drtp/>. That page states no license or redistribution terms, so
check with the authors (contact on that page) before committing any full trace file to a
public repository. Expected layout after extraction:

```
DockerRegistryTraces/
└── data_centers/
    ├── stage-dal09/     stage-dal09-logstash-2017.07.01-0.json, ...
    ├── prestage-mon01/
    ├── fra02/
    └── dev-mon01/
```

Log record format (one JSON array per file):

```json
{"http.request.uri": "v2/<repo-hash>/<repo-hash>/manifests/<ref>",
 "http.request.method": "GET", "http.request.remoteaddr": "<client>",
 "http.request.duration": 0.48, "http.response.written": 917,
 "http.response.status": 200, "id": "...", "timestamp": "2017-07-01T00:09:19.533Z"}
```

See the top-level README for how to build the 41-file sample used for the reported results.
