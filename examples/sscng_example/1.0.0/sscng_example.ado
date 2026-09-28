*! version 1.0.0 27sep2026
program define sscng_example, rclass
    version 16.0
    syntax , Value(real)
    return scalar result = 2 * `value'
end
