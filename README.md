# GeneratedDatasetComparator
GeneratedDatasetComparator main goal is to provide an easy and convenient way to compare a dataset with generated pictures. You can also use it as a tag tool but it doesn't have any auto-tagging features.

![Generated Dataset Comparator](https://i.imgur.com/Ywl4GGP.png)

## Manual installation & Setup
### 1. Clone the Repository
``` bash
git clone https://github.com/ImKyra/GeneratedDatasetComparator
cd GeneratedDatasetComparator
```
### 2. Run the script
For **Linux/macOS**:
``` bash
./run_gui.sh
```
For **Windows**:
``` bash
.\run_gui.bat
```

## File Structure
``` 
/project-directory
│
├── run.py                  # Main file to run the GUI application
├── run_gui.sh              # Shell script for running the app on Linux/Mac
├── run_gui.bat             # Batch script for running the app on Windows
├── DatasetExtractor.py     # Core processing logic for dataset text files
├── requirements.txt        # Python dependency requirements
└── README.md               # Project documentation
```
## Contributing
1. Fork the repository.
2. Create your feature branch (`git checkout -b feature/new-feature`).
3. Commit your changes (`git commit -m 'Add new feature'`).
4. Push your branch (`git push origin feature/new-feature`).
5. Submit a pull request.

## License
This project is licensed under the [MIT License]().
