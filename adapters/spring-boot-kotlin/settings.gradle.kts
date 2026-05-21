rootProject.name = "bugfab-spring"

// The example consumer lives in a nested subproject so a single
// `./gradlew build` exercises the adapter and its quickstart together.
// Toggle by uncommenting if you only want to build the library.
include(":examples:minimal")
