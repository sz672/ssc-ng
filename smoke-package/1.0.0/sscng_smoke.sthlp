{smcl}
{* *! version 1.0.0 28sep2026}{...}
{title:sscng_smoke 1.0.0 -- standalone SSC-NG smoke package}

{title:Syntax}
{p 8 12 2}{cmd:sscng_smoke, value(}{it:number}{cmd:)}{p_end}

{title:Description}
{pstd}This small example prints its version and returns twice the supplied value.
It requires Stata 16.0 or later and has no external package dependencies.{p_end}

{title:Examples}
{phang2}{cmd:. sscng_smoke, value(2)}{p_end}
{pstd}Version 1.0.0 displays 4 and stores 4 in {cmd:r(result)}.
Version 1.1.0 instead displays 5 for the same input.{p_end}
{phang2}{cmd:. return list}{p_end}

{title:Stored results}
{pstd}{cmd:r(result)}: twice the supplied value.{p_end}
{pstd}{cmd:r(package_version)}: {cmd:1.0.0}.{p_end}

{title:License and scope}
{pstd}MIT License; see the included LICENSE file. This is an arithmetic test
fixture for installation, review, version comparison, and restoration. It is
not a statistical analysis package. The metadata contact is illustrative.{p_end}
