* Local SSC-NG checks for Ben Jann's unmodified fre 1.2.5.
* Uses a tiny generated dataset; no downloads or external packages.
version 9.2
clear
input byte rating str5 group
1 "A"
1 "A"
2 "B"
2 "B"
2 "B"
. ""
end

fre rating
assert r(N) == 6
assert r(N_valid) == 5
assert r(N_missing) == 1
assert r(r_valid) == 2
matrix numeric_counts = r(valid)
assert numeric_counts[1,1] == 2
assert numeric_counts[2,1] == 3

fre group
assert r(N) == 6
assert r(N_valid) == 5
assert r(N_missing) == 1
matrix string_counts = r(valid)
assert string_counts[1,1] == 2
assert string_counts[2,1] == 3

fre rating, nomissing
assert r(N) == 5
assert r(N_missing) == 0

fre rating if rating == 2
assert r(N) == 3
assert r(r_valid) == 1
matrix filtered_counts = r(valid)
assert filtered_counts[1,1] == 3

display "fre 1.2.5: numeric, string, missing-value, and filter checks passed"
