version 16.0
sscng_smoke, value(2)
assert r(result) == 4
assert "`r(package_version)'" == "1.0.0"
sscng_smoke, value(-3)
assert r(result) == -6
sscng_smoke, value(0)
assert r(result) == 0
sscng_smoke, value(0.25)
assert r(result) == 0.5
display "sscng_smoke 1.0.0: all arithmetic and version assertions passed"
