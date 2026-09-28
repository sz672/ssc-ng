{smcl}
{* *! version 0.1.0 27sep2026}{...}
{title:Title}

{p 4 4 2}
{cmd:sscng_helper} {hline 2} Arithmetic dependency used by the SSC-NG local pilot.

{title:Syntax}

{p 8 12 2}
{cmd:sscng_helper,} {opt value(#)}

{title:Description}

{p 4 4 2}
Returns twice the supplied value. The package sscng_example version 1.1.0
calls this helper. This fixture has no package dependencies and requires
Stata 16 or later.

{title:Example}

{p 8 12 2}{cmd:. sscng_helper, value(2)}
{p 8 12 2}{cmd:. display r(result)}
{p 8 12 2}4

{title:Stored results}

{p 4 4 2}{cmd:r(result)} contains twice the supplied value.
