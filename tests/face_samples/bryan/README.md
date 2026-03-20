# Bryan Face Samples

This folder contains a dedicated local face-detection sample set for MediaSorter.

Contents:

- `source_resume/`: copied headshot images from `C:\Users\bryan\Documents\resume`
- `source_family/`: one copied family-photo image selected from `H:\media\categories\family photo`
- `crops/`: extracted face crops generated from the source images
- `manifest.json`: source paths, copied paths, bounding boxes, and the family-photo similarity score

Selection notes:

- The family-photo sample is `IMG_0702.HEIC`.
- It was selected by comparing its detected face embedding against the resume-headshot embedding set.
- The best similarity recorded at creation time was approximately `0.9649`.

Primary test coverage:

- `tests/face/test_bryan_face_samples.py`
