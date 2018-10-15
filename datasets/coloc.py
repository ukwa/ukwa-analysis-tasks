import os
import luigi
import luigi.contrib.hdfs
import luigi.contrib.hadoop
import luigi.contrib.hadoop_jar
from luigi.contrib.hdfs.format import Plain, PlainDir
# Dependencies for packaging:
import enum
import botocore


"""
Part of the Alan Turing Institute collaboration on word embeddings generated via word co-location statistics. 

Tasks used to manage generation datasets for researchers.

"""


class GenerateWordColocations(luigi.contrib.hadoop_jar.HadoopJarJobTask):
    """
    This generates word co-location data for a batch of WARCs.

    The data looks like this:

201701  aaaee   rukc    2
201701  aaaejd  gaevc   2
201701  aaaepc  eaamyh  2
201701  aaaepf  eaaezd  2
201701  aaaf    3
201701  aaagb   eaae    4
201701  aaagbrl eaaojj  2
201701  aaagghh through 1
201701  aaagh   endless 7
201701  aaagh   here    7

i.e. a mixture of three-column (frequency) and four-column (co-location) data.

    Parameters:
        input_file: A local file that contains the list of WARC files to process
    """
    input_file = luigi.Parameter()
    task_namespace = "datasets"

    def output(self):
        out_name = "%s-word-coloc.tsv" % os.path.splitext(self.input_file)[0]
        return luigi.contrib.hdfs.HdfsTarget(out_name, format=luigi.contrib.hdfs.Plain)

    #def requires(self):
    #    return tasks.report.crawl_summary.GenerateWarcList(self.input_file)

    def jar(self):
        dir_path = os.path.dirname(os.path.realpath(__file__))
        return os.path.join(dir_path, "../jars/ati-word-colocation-0.0.1-SNAPSHOT-job.jar")

    def main(self):
        return "uk.bl.coloc.wa.hadoop.WARCWordColocationAnalysisTool"

    def args(self):
        return ['-i', self.input_file, '-o', self.output()]


class PreExistingInputFile(luigi.ExternalTask):
    """
    This ExternalTask defines the Target at the top of the task chain. i.e. resources that are overall inputs rather
    than generated by the tasks themselves.

    They are assumed to exist rather than needing an existance check.
    """
    path = luigi.Parameter()
    from_hdfs = luigi.BoolParameter(default=False)

    def complete(self):
        return True

    def output(self):
        """
        Returns the target output for this task.
        In this case, it expects a file to be present in HDFS.
        :return: the target output for this task.
        :rtype: object (:py:class:`luigi.target.Target`)
        """
        if self.from_hdfs:
            return luigi.contrib.hdfs.HdfsTarget(path=self.path)
        else:
            return luigi.LocalTarget(path=self.path)


class GenerateColocDataset(luigi.contrib.hadoop.JobTask):
    '''
    Go through lots of output files in this form:

    ```
    201701<tab>aa<tab>count
    201701<tab>aa<tab>bb<tab>count
    ```

    And split the frequency and colocation data into separate outputs.

    Depends on

     - using a partitioner that splits the data using the leading first part of the key (using a KeyFieldBasedPartitioner)
     - a custom multiple output format to create a different file based on that prefix
     - configuring the reducer so the final key and values are defined correctly, meaning the values are integers.

    '''
    input_file = luigi.Parameter()
    from_hdfs = luigi.BoolParameter(default=True)
    task_namespace = "datasets"


    # Override the default output format, so we can rename the outputs based on teh first key:
    output_format = "uk.bl.wa.hadoop.mapreduce.io.NamedByFirstKeyMultiOutputFormat"

    def requires(self):
        return PreExistingInputFile(path=self.input_file, from_hdfs=True)

    def output(self):
        out_name = "%s-processed" % os.path.splitext(self.input_file)[0]
        return luigi.contrib.hdfs.HdfsTarget(out_name, format=luigi.contrib.hdfs.Plain)

    def mapper(self, line):
        """
        Take the line and create a prefix depending on the type and date range, used to make a filename for the output. Also fix key fields:

        e.g.

            201701<tab>aa<tab>count
            201701<tab>aa<tab>bb<tab>count

        becomes:

            freqn-201701<tab>aa<tab><tab>count
            coloc-201701<tab>aa<tab>bb<tab>count

        :param line:
        :return:
        """
        parts = line.split('\t')
        # Processing a term frequency line, making field count consistent:
        if len(parts) == 3:
            yield "freqn-%s\t%s\t\t%s" % (parts[0], parts[1], parts[2])
        else:
            yield "coloc-%s" % line

    def reducer(self, key, values):
        """
        A simple summation reducer.

        Expects key is e.g. `aa<tab>` and values are counts (for term frequency)
        OR key is e.g. `aa<tab>ab` and values are counts (for term coloc)

        The key prefix, used for partitioning and for naming the output file, should have been dropped at this point.

        :param key:
        :param values:
        :return:
        """
        # Add up the totals:
        yield key, sum(int(v) for v in values)

    def jobconfs(self):
        '''
        Extend the job configuration to support the keys and partitioning we want.
        :return:
        '''
        jc = super(GenerateColocDataset, self).jobconfs()
        # Ensure only the filename-defining part of the key (first value) is used for partitioning:
        jc.append("mapred.text.key.partitioner.options=-k1,1")
        # Ensure the first three fields are all treated as the key:
        jc.append("stream.num.map.output.key.fields=3")
        # Compress the output and the mapper output:
        jc.append("mapred.output.compress=true")
        jc.append("mapred.compress.map.output=true")
        jc.append("mapred.output.compression.codec=org.apache.hadoop.io.compress.GzipCodec")

        return jc

    def job_runner(self):
        '''
        Extend the standard JobRunner to add an additional JAR:
        :return:
        '''
        dir_path = os.path.dirname(os.path.realpath(__file__))
        jar_path = os.path.join(dir_path, "../jars/hadoop-streaming-utils-0.0.1-SNAPSHOT.jar")
        # Get the job runner and add the libjar:
        jr = super(GenerateColocDataset, self).job_runner()
        jr.libjars = [jar_path]

        return jr

    def extra_modules(self):
        '''
        Ensure non-standard Python2.7 modules (that Luigi does not already handle) get packaged:
        :return:
        '''
        return [enum,botocore]


if __name__ == '__main__':
    import logging

    logging.getLogger().setLevel(logging.INFO)
#    luigi.run(['datasets.GenerateWordColocations', '--input-file', 'warcs-2017-frequent-aa'])
    luigi.run(['datasets.GenerateColocDataset', '--input-file', 'warcs-2017-frequent-aa-word-coloc.tsv'])
