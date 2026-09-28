*! version 1.1.0 27sep2026
program define sscng_example, rclass
    version 16.0
    syntax , Value(real)
    sscng_helper, value(`value')
    return scalar result = r(result) + 1
end
