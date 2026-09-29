{smcl}
{* *! version 1.1.0 28sep2026}{...}
{title:sscng_smoke 1.1.0 -- standalone SSC-NG smoke package}

{title:Syntax}
{p 8 12 2}{cmd:sscng_smoke, value(}{it:number}{cmd:)}{p_end}

{title:Description}
{pstd}This small example prints its version and returns twice the supplied value
plus one. It requires Stata 16.0 or later and has no external package dependencies.{p_end}

{title:Examples}
{phang2}{cmd:. sscng_smoke, value(2)}{p_end}
{pstd}Version 1.1.0 displays 5 and stores 5 in {cmd:r(result)}.
Version 1.0.0 instead displays 4 for the same input.{p_end}
{phang2}{cmd:. return list}{p_end}

{title:Stored results}
{pstd}{cmd:r(result)}: twice the supplied value plus one.{p_end}
{pstd}{cmd:r(package_version)}: {cmd:1.1.0}.{p_end}

{title:License and scope}
{pstd}MIT License; see the included LICENSE file. This is an arithmetic test
fixture for installation, review, version comparison, and restoration. It is
not a statistical analysis package. The metadata contact is illustrative.{p_end}
