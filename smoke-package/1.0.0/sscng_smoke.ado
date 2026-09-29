*! version 1.0.0 28sep2026
program define sscng_smoke, rclass
    version 16.0
    syntax , Value(real)
    local answer = 2 * `value'
    display as text "sscng_smoke 1.0.0: 2 * " as result `value' as text " = " as result `answer'
    return scalar result = `answer'
    return local package_version "1.0.0"
end
