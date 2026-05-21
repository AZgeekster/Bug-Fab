<?php

declare(strict_types=1);

namespace BugFab\Laravel\Enums;

enum ReportType: string
{
    case Bug = 'bug';
    case FeatureRequest = 'feature_request';
}
